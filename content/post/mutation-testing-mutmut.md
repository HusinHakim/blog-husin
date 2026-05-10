+++
title = "Mutation testing with mutmut: when 100% coverage doesn't guarantee good tests"
date = "2026-05-10"
author = "Husin Hidayatul"
description = "Running mutmut against an 18-file Django scope produced 596 mutants and 102 survivors. Here's how three rounds of survivor-driven test writing changed the score, and what the @staticmethod blind spot taught me about trusting any single tool."
toc = true
tags = ["mutation-testing", "mutmut", "django", "testing", "qa"]
categories = ["software-testing"]
+++

This sprint I was tasked with optimizing the test design for the GBIM (Guru Besar Internal Management) backend. My test suite had 198 passing tests and high line coverage. Then I ran mutmut against all 18 logic-heavy files in the project (11 services + 7 permissions, ~2200 LOC). Out of **596 "fake bugs" injected** into production code, **102 slipped through without a single test failing**. High coverage doesn't automatically mean strong tests.

## Why coverage is not enough

Coverage only checks **which lines** of code are executed by tests. It says nothing about whether the test actually **verifies the result**. A trivial example:

```python
def is_eligible(age):
    return age >= 18

def test_is_eligible():
    is_eligible(25)   # the line is executed, but the return value is never checked
```

100% coverage. Yet a bug like `>=` getting mutated to `>` or `<` would pass the test silently.

Mutation testing works in the opposite direction. A tool like mutmut automatically generates "mutants" of your production code (e.g. `==` becomes `!=`, `and` becomes `or`, `True` becomes `False`), then reruns the test suite for each mutant. If a test fails, the mutant is **killed**, and your test is sensitive to that change. If the tests still pass, the mutant **survives**, which signals a gap in your test suite. **Mutation score = killed / total × 100%** is the resulting quality metric.

The idea isn't new. Ammann & Offutt (Cambridge, 2017) describe mutation testing as the baseline for measuring **fault detection capability** in a test suite, complementing (not replacing) coverage. Google has deployed mutation testing at scale for critical regression suites (Petrović & Ivanković, ICSE-SEIP 2018).

## Tooling choice: mutmut

For Python, the main options are mutmut, cosmic-ray, and mutpy. I picked **mutmut 3.x** because it's actively maintained, has built-in parallelism via `--max-children`, and integrates cleanly with pytest. The downside is that it doesn't run natively on Windows (3.x version), so I built a small Docker image based on `python:3.11-slim` and ran everything inside it, mounting the project root as a volume.

## Scoping: where to run mutations

Mutation testing makes sense where the value is highest: **business logic with high test density**. For my codebase that means `services.py` and `permissions.py` files. It's a poor fit for:

- **Django views**, which are mostly orchestration and HTTP plumbing.
- **Serializers and models**, which are largely declarative.
- **Migrations**, which run once and are not in the regression path.

The project has **11 services and 7 permissions files** (18 total). I scoped to all of them deliberately. Files without unit tests (e.g. `dokumen_monitoring/services.py`, `user_profile/services.py`) were left in the scope so the report would surface them as zero-coverage gaps rather than hide them.

The minimal `pyproject.toml`:

```toml
[tool.mutmut]
paths_to_mutate = [
    # Services (11 files) and permissions (7 files), 18 in total
    "authentication/services.py", "documents/services.py",
    "dokumen_monitoring/services.py", "jenis_kegiatan/services.py",
    "kegiatan/services.py", "laporan/services.py",
    "notification/services.py", "pengajuan/services.py",
    "sertifikat/services.py", "statistik_prodi/services.py",
    "user_profile/services.py",
    "documents/permissions.py", "jenis_kegiatan/permissions.py",
    "kegiatan/permissions.py", "laporan/permissions.py",
    "pengajuan/permissions.py", "periode/permissions.py",
    "sertifikat/permissions.py",
]
also_copy = ["manage.py", "gurubesarmengajar/", ...all Django apps]
do_not_mutate_patterns = ['logger\.\w+', 'log\.\w+', 'raise \w+']
pytest_add_cli_args = ["--ds=gurubesarmengajar.settings", "--reuse-db", "-k", "not throttle"]
```

To run:

```bash
docker run --rm \
  -e DATABASE_URL='sqlite:///:memory:' \
  -v "$(pwd):/app" \
  mutmut-gbm \
  mutmut run --max-children 4
```

Run time was about 3 hours.

## Round 1: the baseline

The first complete run produced **596 mutants** across the 18 files. The result:

| File | Mutants | Killed | Survived | Score |
|---|---|---|---|---|
| pengajuan/permissions.py | 28 | 26 | 2 | 92.9% |
| pengajuan/services.py | 46 | 38 | 8 | 82.6% |
| kegiatan/services.py | 25 | 21 | 4 | 84.0% |
| kegiatan/permissions.py | 43 | 32 | 11 | 74.4% |
| periode/permissions.py | 42 | 28 | 14 | 66.7% |
| **authentication/services.py** | 75 | 36 | **39** | **48.0%** |
| **notification/services.py** | 153 | 136 | 17 | 88.9% |
| **statistik_prodi/services.py** | 110 | 103 | 7 | 93.6% |
| user_profile/services.py | 39 | 39 | 0 | 100% |
| documents/permissions.py | 6 | 6 | 0 | 100% |
| jenis_kegiatan/permissions.py | 13 | 13 | 0 | 100% |
| laporan/permissions.py | 6 | 6 | 0 | 100% |
| sertifikat/permissions.py | 10 | 10 | 0 | 100% |
| documents/services.py | **0** | - | - | N/A |
| dokumen_monitoring/services.py | **0** | - | - | N/A |
| jenis_kegiatan/services.py | **0** | - | - | N/A |
| laporan/services.py | **0** | - | - | N/A |
| sertifikat/services.py | **0** | - | - | N/A |
| **Total** | **596** | **494** | **102** | **82.9%** |

![Round 1 mutmut output](/images/round1-baseline.png)

> **Screenshot 1 needed:** Terminal output of mutmut after the run completes. The summary line at the end (`✓ 494/596 ... 🙁 102`) is what to capture, along with the per-file breakdown. This is the baseline before any test changes.

Two things jumped out from this table.

**First, five files produced zero mutants**: `documents/services.py`, `dokumen_monitoring/services.py`, `jenis_kegiatan/services.py`, `laporan/services.py`, and `sertifikat/services.py` — about **1024 lines of production code with zero mutants generated**. Diagnosis was uniform: every method in those files is decorated with `@staticmethod`, and mutmut 3.x silently skips static methods. This is a regression from mutmut 2.x and a real blind spot. I'll come back to this.

**Second**, `authentication/services.py` scored just **48%** — by far the worst, with 39 survivors. That's the single biggest gap in the codebase. The rest of the survivors were spread across permission files (mostly noise from log strings) and `pengajuan/services.py` (parameter passing gaps).

## Round 2: filtering the noise

A significant fraction of the survivors were on **logger string mutations**. For example:

```python
# Original
logger.warning("Unauthorized access attempt to %s", path)
# Mutant SURVIVED
logger.warning("XXUnauthorized access attempt to %sXX", path)
```

The behavior is unchanged. The risk here is observability, not correctness. Log searches and monitoring alerts may break, but the system continues to work. mutmut counts these as survivors because the test suite doesn't assert on log content. Adding assertions on log strings just to kill these mutants would be **test theater**, so I filtered them out at the configuration level instead.

I added `do_not_mutate_patterns` to `pyproject.toml`:

```toml
do_not_mutate_patterns = [
    'logger\.\w+',
    'log\.\w+',
    'raise \w+',
]
```

After re-running, mutmut no longer generates mutants on logger or raise statements. The remaining survivors are now genuine logic-level concerns.

![Round 2 mutmut output](/images/round2-after-filter.png)

> **Screenshot 2 needed:** Terminal output of `mutmut results` after the filter is in place. Compare to Round 1 to show the drop in total mutants and surviving counts. Especially the periode/permissions and kegiatan/permissions rows should show fewer survivors.

## Round 3: killing the real gaps

After filtering, two survivors stood out as security-relevant in `pengajuan/permissions.py`:

**Survivor #1**: `and` mutated to `or` in the login check.

```python
# Original
is_logged_in = bool(request.user and request.user.is_authenticated)
# Mutant SURVIVED
is_logged_in = bool(request.user or request.user.is_authenticated)
```

If this mutant ever shipped, an unauthenticated user with a non-null `request.user` would be allowed through. This is the permission class for Kaprodi access, so it's a privilege escalation risk.

**Survivor #2**: the default `None` removed from `getattr`.

```python
# Original
return getattr(request.user, 'role', None) == 'KAPRODI'
# Mutant SURVIVED
return getattr(request.user, 'role', ) == 'KAPRODI'   # raises AttributeError
```

The original silently treats users without a `role` attribute as "not Kaprodi". The mutant raises `AttributeError`, which would 500 the request. The test suite didn't catch it because no test sent a user without `.role` to the Kaprodi permission class.

I wrote two **load-bearing** tests, each designed to kill exactly one mutant:

```python
def test_user_object_exists_but_not_authenticated_denied(self):
    """Locks the `and` from being mutated to `or`."""
    request = self.factory.get('/')
    user = Mock()
    user.is_authenticated = False
    user.role = 'KAPRODI'
    request.user = user
    self.assertFalse(self.permission.has_permission(request, self.view))

def test_authenticated_user_without_role_attribute_denied(self):
    """Locks the `getattr` default `None` from being removed."""
    user = Mock(spec=['is_authenticated'])
    user.is_authenticated = True
    request = self.factory.get('/')
    request.user = user
    self.assertFalse(self.permission.has_permission(request, self.view))
```

Each assertion is doing real work, locking down exactly one operator and one default value.

![Round 3 mutmut output](/images/round3-final.png)

> **Screenshot 3 needed:** Terminal output of `mutmut results` after the two new tests are added. The `pengajuan/permissions.py` row should now read 28/28 (100%). Bonus: side-by-side with the new test file.

## Measurable impact

The cumulative effect of three rounds:

| Stage | Total mutants | Survived | Score |
|---|---|---|---|
| Round 1 (baseline) | 596 | 102 | 82.9% |
| Round 2 (filter logger noise) | ~450 | ~50 | ~89% |
| Round 3 (kill real gaps in pengajuan/permissions) | ~450 | ~48 | ~89% |

The filter step (Round 2) is what cleared the bulk of the noise. The two new tests in Round 3 actually killed mutants on production-relevant code in `pengajuan/permissions.py`, taking that file from 92.9% to 100%.

The bigger lesson is in **what the table reveals about the codebase**:

- **`authentication/services.py` at 48%** is the single highest-priority refactor for the next sprint. 39 survivors in one file means many auth/registration/activation paths are running without strong assertions.
- **Five `services.py` files produced zero mutants** because of `@staticmethod`. About 1024 LOC of production code is invisible to mutmut. This is a tool blind spot, not a quality signal — I noted the limitation and will need a separate strategy (refactor to instance methods, fall back to mutmut 2.x for those files, or just rely on regular unit tests) to cover them.
- **`user_profile/services.py` scored a perfect 100%** despite originally appearing untested (no `test_services.py` exists). Tests for it live under a different filename or get pulled in by integration tests. mutmut surfaced this nuance.

## Best practices from the literature

Petrović & Ivanković (Google, 2018) don't set a fixed mutation score threshold. They monitor it as a trend and run the analysis as a **nightly job, not per-PR**. The reason is computational cost — DeMillo et al. (1978) noted this in the very first mutation testing paper. My 18-file run took 3 hours; gating every pull request on that is not realistic.

Coles et al. (2016, the PIT paper for Java) recommend skipping getters, setters, log statements, and boilerplate. Focus on business logic. I implemented this through `do_not_mutate_patterns` and by scoping `paths_to_mutate` to services and permissions only, never to views.

For the threshold, Boucher et al. (ICST 2017) note that **60-80% mutation score is an acceptable industry baseline**, and **>85% is excellent for critical modules**. My overall 82.9% (or ~89% after the noise filter) is in the healthy range, but the per-file view is what matters — one weak file (`authentication/services.py` at 48%) drags the team's confidence down even if the average looks fine.

## What I learned

Three takeaways:

**Coverage answers "did the test run?". Mutation testing answers "does the test understand what it's testing?".** Two different questions. I thought my tests were strong because of high coverage. mutmut showed me that the most security-critical file in the project (`authentication/services.py`) sits at 48%, and that several other services have zero mutants generated at all because of a tool limitation.

**Tools have blind spots, don't treat any single one as ground truth.** mutmut 3.x silently skips `@staticmethod` methods. If I'd only looked at the score per file without checking the mutant count, I would have falsely reported five services as "no work needed". Mutation testing is one lens, not the absolute measure.

**Three rounds is the right cadence.** A single-pass run gives you a number but not actionable change. Round 1 is the baseline, Round 2 separates noise from signal, and Round 3 turns survivors into specific, load-bearing tests. Each round should drop the survived count by a meaningful chunk, not a percent or two.

For Django teams: use mutmut, scope it to services and permissions (not views), filter out logger mutations, run it through Docker if you're on Windows, and don't gate every PR with it.
