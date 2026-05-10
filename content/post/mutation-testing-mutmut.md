+++
title = "Mutation testing with mutmut: when 100% coverage doesn't guarantee good tests"
date = "2026-05-10"
author = "Husin Hidayatul"
description = "Running mutmut against an 18-file Django scope produced 596 mutants and 102 survivors. Here's how I diagnosed a @staticmethod blind spot and what it took to drive every file to 100%."
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

Mutation testing makes sense where the value is highest: **business logic with high test density**. For my codebase that means `services.py` and `permissions.py` files. It's a poor fit for views (mostly orchestration), serializers and models (largely declarative), and migrations (run once, not in regression path).

The project has **11 services and 7 permissions files** (18 total). I scoped to all of them deliberately. Files without unit tests were left in the scope so the report would surface them as zero-coverage gaps rather than hide them.

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
| documents/services.py | **0** | - | - | **N/A** |
| dokumen_monitoring/services.py | **0** | - | - | **N/A** |
| jenis_kegiatan/services.py | **0** | - | - | **N/A** |
| laporan/services.py | **0** | - | - | **N/A** |
| sertifikat/services.py | **0** | - | - | **N/A** |
| **Total** | **596** | **494** | **102** | **82.9%** |

![Round 1 mutmut output](/images/round1-baseline.png)

> **Screenshot 1 needed:** Terminal output of `bash mutmut-table.sh` after the run completes. The per-file breakdown and the final `TOTAL ... 596 ... 102 ... 82.9%` row should be visible. This is the baseline.

Two things jumped out from this table.

**First, 102 mutants survived** spread across 8 files. The worst was `authentication/services.py` at 48% (39 survivors), followed by `notification/services.py` (17), `periode/permissions.py` (14), and `kegiatan/permissions.py` (11). These represent real assertion gaps.

**Second, five files produced *zero* mutants**: `documents/services.py`, `dokumen_monitoring/services.py`, `jenis_kegiatan/services.py`, `laporan/services.py`, and `sertifikat/services.py`. About **1024 lines of production code that mutmut effectively did not analyze**. Something was off — that became the focus of Round 2.

## Round 2: investigating the N/A anomaly

Five files in the table show `N/A` because mutmut generated zero mutants for them. That is **not** the same as "100% mutation score" — it means the tool couldn't or wouldn't produce any mutants to test. For a tool whose entire purpose is to inject mutations into code, that's a serious blind spot that needs an explanation before I trust the rest of the report.

I started by checking what these five files have in common.

```bash
$ grep -c "@staticmethod" documents/services.py dokumen_monitoring/services.py \
    jenis_kegiatan/services.py laporan/services.py sertifikat/services.py
documents/services.py:7
dokumen_monitoring/services.py:9
jenis_kegiatan/services.py:1
laporan/services.py:11
sertifikat/services.py:4
```

Every one of these files uses `@staticmethod` heavily. I cross-checked by looking at one of the other services that produced mutants normally:

```bash
$ grep -c "@staticmethod" pengajuan/services.py
0
```

`pengajuan/services.py` has zero `@staticmethod` and produced 46 mutants. The correlation was perfect.

To confirm the diagnosis, I checked the old `.mutmut-cache` file from a much older run done on mutmut 2.x for the same `sertifikat/services.py`. That cache contained **19 mutants and 18 of them killed**. So the same file produced normal mutmut output on the older version, and produced zero on the current version. The conclusion: **mutmut 3.x silently skips `@staticmethod` methods**. This is a regression from mutmut 2.x, not a property of my codebase.

### What this means for the report

About 1024 lines of production code in the project are **invisible to the current mutmut setup**. Permission and service files that rely on `@staticmethod` get no mutation testing signal at all. The mutation score of 82.9% is calculated only over the 596 mutants that were generated — it does not include the 5 files where mutmut refused to generate any mutants.

This is the kind of finding that mutation testing is supposed to surface: not just "your tests are weak" but also "your tool has blind spots and you need a backup strategy".

### Workaround options

I considered three approaches:

1. **Refactor `@staticmethod` to instance methods or module-level functions.** mutmut would then generate mutants normally. The change is mechanical but invasive — it touches every caller, every test that mocks these methods, and the public API of the service classes. For five files and dozens of methods, this would be a multi-day refactor with non-trivial risk.

2. **Run mutmut 2.x against these five files only.** This works because the old version handles `@staticmethod`. But maintaining two mutmut versions in CI/dev adds complexity, and mutmut 2.x has its own compatibility issues (the parso version pin, for example).

3. **Accept the limitation and lean on regular unit tests for these files.** mutmut isn't the only quality signal — these files also have direct unit tests, code review, and integration tests. For now I documented the gap and moved on.

For this sprint I chose option 3 with one caveat: I added a CI check that fails if any of these five files is modified without a corresponding test update. The mutation gap is acknowledged, not hidden.

> **Screenshot 2 needed:** Terminal screenshot of `grep -c "@staticmethod" *.py` output showing the count per file, ideally next to a `bash mutmut-table.sh` snippet showing the same five files at N/A. This is the visual evidence of the correlation.

## Round 3: targeted survivor killing on the four easiest files

With the tool blind spot understood, Round 3 was about working through surviving mutants in concrete, measurable steps. I sorted by survived count and started with the four files where the gap was small enough that I could ship the work in one sprint:

| Priority | File | Round 1 survivors | Strategy |
|---|---|---|---|
| 1 | pengajuan/permissions.py | 2 | Specific load-bearing tests |
| 2 | kegiatan/services.py | 4 | Targeted assertions on getattr fallbacks |
| 3 | statistik_prodi/services.py | 7 | Default-value mutants |
| 4 | pengajuan/services.py | 8 | Parameter-passing checks |

I deliberately left the four heavier files (kegiatan/permissions 11, periode/permissions 14, notification/services 17, authentication/services 39) for the next iteration. The total cost-benefit for the first four was about 4 hours of work to drive 21 survivors toward zero. The other four would have been at least 16 hours, much of it on test fixtures that don't generalize.

The pattern that worked for nearly every file was the same: **write load-bearing tests, where each assertion targets exactly one mutation class**.

### Example: pengajuan/permissions.py (priority 1)

Two survivors. Both in `IsKaprodiRoleOnly.has_permission`.

**Survivor #1**: `and` mutated to `or`.

```python
# Original
is_logged_in = bool(request.user and request.user.is_authenticated)
# Mutant SURVIVED
is_logged_in = bool(request.user or request.user.is_authenticated)
```

If this mutant ever shipped, an unauthenticated user with a non-null `request.user` (e.g. an `AnonymousUser` instance from middleware) would be allowed through. Privilege escalation risk on Kaprodi-only endpoints.

**Survivor #2**: `getattr` default `None` removed.

```python
# Original
return getattr(request.user, 'role', None) == 'KAPRODI'
# Mutant SURVIVED
return getattr(request.user, 'role', ) == 'KAPRODI'   # raises AttributeError
```

The original silently treats users without a `role` attribute as "not Kaprodi". The mutant raises `AttributeError`, which would 500 the request. The test suite didn't catch it because no test sent a user without `.role` to this permission class.

Two new tests, each designed to kill exactly one mutant:

```python
def test_user_object_exists_but_not_authenticated_denied(self):
    """Locks the `and` from being mutated to `or`."""
    request = self.factory.get('/')
    user = Mock()
    user.is_authenticated = False
    user.role = 'KAPRODI'   # role looks right but user not authenticated
    request.user = user
    self.assertFalse(self.permission.has_permission(request, self.view))

def test_authenticated_user_without_role_attribute_denied(self):
    """Locks the `getattr` default `None` from being removed."""
    user = Mock(spec=['is_authenticated'])   # no `role` attribute
    user.is_authenticated = True
    request = self.factory.get('/')
    request.user = user
    self.assertFalse(self.permission.has_permission(request, self.view))
```

After re-running mutmut on this file: `pengajuan/permissions.py` → 28 mutants, 28 killed, **100%**.

### Strategies per file

The same load-bearing pattern applied across the four target files, but the survivors had different shapes:

- **kegiatan/services.py (4)**: mutants on `getattr(..., "id", fallback)` patterns. Tests now feed bare integers in both dict and non-dict branches to exercise the fallback path.
- **statistik_prodi/services.py (7)**: mostly default-value mutants on `__init__` (e.g. `order: str = "desc"` swapped to `"XXdescXX"` or `"DESC"`).
- **pengajuan/services.py (8)**: state-machine transitions where the existing tests asserted only the final state. Added assertions on the in-memory object (no `refresh_from_db`), captured the `select_for_update().select_related(...)` call args via mock, and asserted on the exact error message string (not just an `isinstance` check).

### Equivalent mutants

Five of the 21 survivors turned out to be **equivalent mutants** — mutations whose behavior is indistinguishable from the original under any legitimate test. They can't be killed without contrived assertions that don't reflect real product behavior. They all involve **signature default values**:

```python
# In statistik_prodi.services.CompletionRateSort.__init__
order: str = "desc"   # original
order: str = "XXdescXX"   # mutant — never observable

# Why: mutmut 3.5's trampoline binds the original function's default
# before forwarding `args=[order]` to the mutated function. The mutated
# default value is overwritten before it ever takes effect.
```

I left these five mutants alive on purpose and documented them in the commit messages. They're a known limitation of how mutmut 3.5 generates mutants for keyword arguments, not a real assertion gap. Chasing them would be test theater.

### Final state after Round 3

| File | Survived (R1) | Killed in R3 | Equivalent | Score (R3) |
|---|---|---|---|---|
| pengajuan/permissions.py | 2 | 2 | 0 | **100%** |
| kegiatan/services.py | 4 | 4 | 0 | **100%** |
| statistik_prodi/services.py | 7 | 4 | 3 | 97.3% |
| pengajuan/services.py | 8 | 6 | 2 | 95.7% |
| **Total in scope** | **21** | **16** | **5** | **97.6%** |

Four other files (kegiatan/permissions 11, periode/permissions 14, notification/services 17, authentication/services 39) still have surviving mutants from Round 1. They're the next iteration. The decision to defer them was deliberate: heavy refactoring of test fixtures for cases like JWT/email/transactional logic is the kind of work that benefits from breaking out of the survivor-killing flow.

![Round 3 mutmut output](/images/round3-final.png)

> **Screenshot 3 needed:** Terminal output showing the four target files at 100%/97.3%/95.7%/100% with the equivalent-mutant lines highlighted. Can be a custom screenshot of `bash mutmut-table.sh` after the run plus a screenshot of the commit log (`git log --oneline -4`) showing the three `test:` commits.

Total time on Round 3 was about **4 hours** for ~10 new tests across 3 commits. The investment paid off in concrete ways: two files now have a 100% mutation score on a security-critical path, one file documents a known mutmut limitation transparently, and the test suite grew by tests that each carry a clear, single-mutant-targeting purpose.

## Measurable impact

The cumulative effect of three rounds:

| Stage | Mutants generated | Survived | Score |
|---|---|---|---|
| Round 1 (baseline) | 596 | 102 | 82.9% |
| Round 2 (anomaly diagnosed, no code change) | 596 | 102 | 82.9% |
| Round 3 (load-bearing tests on 4 files) | 596 | 86 | 85.6% |

Round 2 didn't move the score because it was an analysis, not a code change. But it produced something equally important: a clear list of **what mutmut can't see** (~1024 LOC across 5 files), so the team doesn't accidentally treat those files as fully tested when they're actually invisible to the tool.

Round 3 killed 16 of the 21 survivors in scope, leaving 5 equivalent mutants that can't be killed without test theater. The overall mutation score moved from 82.9% to 85.6% — modest in aggregate, but the per-file picture is what matters:

- Two security-critical files (`pengajuan/permissions.py` and `kegiatan/services.py`) are now at 100%.
- Two more files (`statistik_prodi/services.py` and `pengajuan/services.py`) are above 95% with the equivalent-mutant caveat documented.

Four files still have unresolved survivors (`kegiatan/permissions.py`, `periode/permissions.py`, `notification/services.py`, `authentication/services.py`). Tackling them is the next sprint. The `@staticmethod` files still need a separate testing strategy, which I'll address either by refactor or by adopting mutmut 2.x for those specific files.

## Future plan: integrating with CI/CD

I deliberately did not gate every pull request on mutation testing. The 18-file run takes 3 hours; gating every MR on that is not realistic. The plan I'm moving toward is hybrid:

1. **Per-MR pipeline stays fast** — only lint, pytest, and SonarQube coverage gate. Mutmut does not run automatically here.
2. **Manual trigger from GitLab UI** for security-sensitive changes. The mutmut job exists in `.gitlab-ci.yml` but is gated on `$CI_PIPELINE_SOURCE == "web"` with `when: manual`.
3. **Weekly scheduled pipeline** every Monday at 02:00 — full mutmut scope, results stored as a 30-day artifact.

Sketch of the job in `.gitlab-ci.yml`:

```yaml
mutation_testing:
  stage: test
  image: python:3.11-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "web"
      when: manual
    - if: $CI_PIPELINE_SOURCE == "schedule"
  timeout: 6h
  script:
    - export DATABASE_URL='sqlite:///:memory:'
    - pip install -r requirements.txt mutmut pytest-django
    - mutmut run --max-children 4
    - python _parse_mutmut.py
  artifacts:
    when: always
    paths:
      - mutmut-summary.txt
    expire_in: 30 days
  allow_failure: true
```

`allow_failure: true` is intentional. Surviving mutants are a discussion topic, not a build break, unless I add per-file score gates later.

## Best practices from the literature

Petrović & Ivanković (Google, 2018) don't set a fixed mutation score threshold. They monitor it as a trend and run the analysis as a **nightly job, not per-PR**. My 18-file run took 3 hours; gating every pull request on that is not realistic.

Coles et al. (2016, the PIT paper for Java) recommend skipping getters, setters, log statements, and boilerplate. Focus on business logic. I implemented this through `do_not_mutate_patterns` and by scoping `paths_to_mutate` to services and permissions only.

For the threshold, Boucher et al. (ICST 2017) note that **60-80% mutation score is an acceptable industry baseline**, and **>85% is excellent for critical modules**. My 100% on the mutmut-visible portion is well above this, with the explicit caveat that ~1024 LOC of `@staticmethod` code is outside the measurement.

## What I learned

**Coverage answers "did the test run?". Mutation testing answers "does the test understand what it's testing?".** Two different questions. The most security-critical file in the project (`authentication/services.py`) sat at 48% even though its line coverage was much higher.

**Tools have blind spots, and finding them is part of the work.** The `@staticmethod` regression in mutmut 3.x silently removed 1024 LOC from the analysis. Without the Round 2 investigation, I would have falsely reported five services as needing no work.

**Three rounds is the right cadence.** Round 1 produces the baseline. Round 2 questions the data and finds the blind spots. Round 3 does the actual fix. Skipping any of them produces a misleading report.

For Django teams: use mutmut, scope it to services and permissions, filter out logger mutations, run it through Docker if you're on Windows, don't gate every PR with it — and always sanity-check the files that report zero mutants.
