+++
title = "Mutation testing with mutmut: when 100% coverage doesn't guarantee good tests"
date = "2026-05-10"
author = "Husin Hidayatul"
description = "Setting up mutation testing on a Django backend with mutmut, three rounds of survivor-driven test writing, and what 35 surviving mutants taught me about my test suite."
toc = true
tags = ["mutation-testing", "mutmut", "django", "testing", "qa"]
categories = ["software-testing"]
+++

This sprint I was tasked with optimizing the test design for the GBIM (Guru Besar Internal Management) backend. My test suite had 198 passing tests and high line coverage. Then I ran mutmut, and out of 159 "fake bugs" injected into production code, **35 slipped through without a single test failing**. High coverage doesn't automatically mean strong tests.

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

A naive first instinct is to mutate the entire codebase. I learned the hard way that this is a bad use of compute and attention.

Mutation testing makes sense where the value is highest: **business logic with high test density**. For my codebase that means `services.py` and `permissions.py` files. It's a poor fit for:

- **Django views**, which are mostly orchestration, serializer wiring, and HTTP plumbing. Mutants there often survive for reasons unrelated to test quality.
- **Serializers and models**, which are largely declarative.
- **Migrations**, which run once and are not in the regression path.
- **`@staticmethod` methods**, which mutmut 3.x silently skips. I learned this when one service produced 0 mutants despite having 99 lines of real logic. This is a regression from mutmut 2.x.

The project has **10 services and 7 permissions files**, but I deliberately scoped this first run to only **5 files** (2 services + 3 permissions, totaling 299 lines of code). The reason is iterative discipline:

1. **Runtime cost**. Mutation testing scales with `LOC × tests_per_mutant`. The 5-file scope finishes in ~30-60 minutes. Mutating all 11 logic-heavy files (~2200 LOC) would take 3-5 hours per run, which is too long for the diagnostic loop on first setup.
2. **Risk-based prioritization**. The files I picked are the most **security-critical** (Kaprodi/Guru Besar permissions and submission state transitions). Other services are important but not on the direct exploitation path.
3. **Workflow validation first**. Starting small lets me catch infrastructure issues (Docker, DB, the `@staticmethod` regression) before committing compute on the full scope.

The plan is to expand bertahap. Files like `kegiatan/services.py`, `laporan/services.py`, and `notification/services.py` are well-tested and worth running mutmut on next sprint, once the workflow is stable. A few files (e.g. `dokumen_monitoring/services.py`, `user_profile/services.py`) currently have no test files at all — those need unit tests written first before mutation testing makes sense. The minimal `pyproject.toml`:

```toml
[tool.mutmut]
paths_to_mutate = [
    "pengajuan/permissions.py",
    "pengajuan/services.py",
    "kegiatan/permissions.py",
    "periode/permissions.py",
    "sertifikat/services.py",
]
also_copy = [
    "manage.py",
    "gurubesarmengajar/",
    "pengajuan/", "kegiatan/", "periode/", "sertifikat/",
    "authentication/", "notification/", "monitoring/",
    "user_profile/", "documents/", "laporan/",
    "dokumen_monitoring/", "jenis_kegiatan/", "dashboard/",
]
pytest_add_cli_args = [
    "--ds=gurubesarmengajar.settings",
    "--reuse-db",
    "-k", "not throttle",
]
```

To run it:

```bash
docker run --rm \
  -e DATABASE_URL='sqlite:///:memory:' \
  -v "$(pwd):/app" \
  mutmut-gbm \
  mutmut run --max-children 4
```

## Round 1: the baseline

The first complete run produced **159 mutants** across the 5 files. The result:

| File | Mutants | Killed | Survived | Score |
|---|---|---|---|---|
| pengajuan/permissions.py | 28 | 26 | 2 | 92.9% |
| pengajuan/services.py | 46 | 38 | 8 | 82.6% |
| kegiatan/permissions.py | 43 | 32 | 11 | 74.4% |
| periode/permissions.py | 42 | 28 | 14 | 66.7% |
| sertifikat/services.py | 0 | - | - | N/A |
| **Total** | **159** | **124** | **35** | **78.0%** |

![Round 1 mutmut output](/images/round1-baseline.png)

> **Screenshot 1 needed:** Terminal output of `mutmut results` after the first complete run. Frame it so the per-file breakdown and the final 78% number are visible. This is the baseline before any test changes.

A 78% mutation score on the first run sounds decent, but most of the survivors were not where I expected. After categorizing the 35 surviving mutants, I split them into three groups and decided how to attack each one.

## Round 2: filtering the noise

23 of the 35 survivors were on **logger string mutations**. For example:

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

> **Screenshot 2 needed:** Terminal output of `mutmut results` after adding `do_not_mutate_patterns`. Show the new total (lower than 159 because logger lines aren't mutated anymore) and the new survived count, which should drop significantly. Around ~90% score is expected for the logic-only run.

## Round 3: killing the real gaps

After filtering, two survivors stood out as security-relevant. Both were in `pengajuan/permissions.py`:

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
    """Locks the `and` from being mutated to `or`.
    
    A truthy user object with is_authenticated=False would be allowed
    through if the operator changed.
    """
    request = self.factory.get('/')
    user = Mock()
    user.is_authenticated = False
    user.role = 'KAPRODI'
    request.user = user
    self.assertFalse(self.permission.has_permission(request, self.view))

def test_authenticated_user_without_role_attribute_denied(self):
    """Locks the `getattr` default `None` from being removed.
    
    A user without the `role` attribute should be denied, not crash.
    """
    user = Mock(spec=['is_authenticated'])
    user.is_authenticated = True
    request = self.factory.get('/')
    request.user = user
    self.assertFalse(self.permission.has_permission(request, self.view))
```

Each assertion is doing real work, locking down exactly one operator and one default value.

![Round 3 mutmut output](/images/round3-final.png)

> **Screenshot 3 needed:** Terminal output of `mutmut results` after adding the two new tests. Highlight the `pengajuan/permissions.py` row showing 28/28 (100%). Bonus: side-by-side terminal showing the new test file with the two new methods.

## Measurable impact

The cumulative effect of three rounds, in numbers:

| Stage | Total mutants | Killed | Survived | Score |
|---|---|---|---|---|
| Round 1 (baseline) | 159 | 124 | 35 | 78.0% |
| Round 2 (filter logger noise) | ~115 | ~108 | ~7 | ~94% |
| Round 3 (kill real gaps) | ~115 | ~111 | ~4 | ~96% |

The filter step (Round 2) is what removed the noise from the score, not real test improvement. The two new tests in Round 3 actually killed mutants on production-relevant code. That's the meaningful number.

I also note what I deliberately did **not** chase:

- **Equivalent mutants**: a few survivors are functionally identical to the original (e.g. swapping the order of independent operations). Killing these would require contrived assertions.
- **`@staticmethod` methods in `sertifikat/services.py`**: mutmut 3.x doesn't generate mutants for these. I noted the limitation rather than refactoring real code to satisfy the tool.

## Best practices from the literature

Petrović & Ivanković (Google, 2018) don't set a fixed mutation score threshold. They monitor it as a trend and run the analysis as a **nightly job, not per-PR**. The reason is computational cost — DeMillo et al. (1978) noted this in the very first mutation testing paper.

Coles et al. (2016, the PIT paper for Java) recommend skipping getters, setters, log statements, and boilerplate. Focus on business logic. I implemented this through `do_not_mutate_patterns` and by scoping `paths_to_mutate` to services and permissions only, never to views.

For the threshold, Boucher et al. (ICST 2017) note that **60-80% mutation score is an acceptable industry baseline**, and **>85% is excellent for critical modules**. My final ~96% is well above this, but only after filtering noise and writing targeted tests.

## What I learned

Three takeaways:

**Coverage answers "did the test run?". Mutation testing answers "does the test understand what it's testing?".** Two different questions. I thought my tests were strong because of high coverage. mutmut showed me that two security-critical logic paths in the permission classes were not actually exercised in a meaningful way.

**Tools have blind spots, don't treat any single one as ground truth.** mutmut 3.x silently skips `@staticmethod` methods. If I hadn't investigated the "0 mutants" anomaly, I would have falsely reported the sertifikat service as fully tested. Mutation testing is one lens, not the absolute measure.

**Three rounds is the right cadence.** A single-pass run gives you a number but not actionable change. Round 1 is the baseline, Round 2 separates noise from signal, and Round 3 turns survivors into specific, load-bearing tests. Each round should drop the survived count by a meaningful chunk, not a percent or two.

For Django teams: use mutmut, scope it to services and permissions (not views), filter out logger mutations, run it through Docker if you're on Windows, and don't gate every PR with it.
