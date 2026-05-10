+++
title = "Mutation testing with mutmut: when 100% coverage doesn't guarantee good tests"
date = "2026-05-10"
author = "Husin Hidayatul"
description = "Running mutmut against an 18-file Django scope produced 596 mutants and 102 survivors. Here's what three rounds of survivor-driven testing revealed."
toc = true
tags = ["mutation-testing", "mutmut", "django", "testing", "qa"]
categories = ["software-testing"]
+++

This sprint I ran mutmut against all 18 logic-heavy files in our Django backend (11 services + 7 permissions). Out of **596 "fake bugs" injected** into production code, **102 slipped through** the test suite. The test suite had 198 passing tests and high line coverage. High coverage doesn't automatically mean strong tests.

## Why coverage is not enough

Coverage only checks **which lines** of code are executed by tests. It says nothing about whether the test actually **verifies the result**.

```python
def is_eligible(age):
    return age >= 18

def test_is_eligible():
    is_eligible(25)   # the line is executed, but the return value is never checked
```

100% coverage. Yet a bug like `>=` mutated to `>` or `<` would pass the test silently.

Mutation testing inverts this. The tool generates "mutants" of production code (e.g. `==` → `!=`, `and` → `or`, `True` → `False`) and reruns the tests for each one. If a test fails, the mutant is **killed**. If tests still pass, the mutant **survives**, which signals a gap in your assertions. **Mutation score = killed / total**.

Ammann & Offutt (Cambridge, 2017) describe mutation testing as the baseline for measuring **fault detection capability**, complementing coverage. Google has deployed it at scale for critical regression suites (Petrović & Ivanković, ICSE-SEIP 2018).

## Tooling choice: mutmut

I picked **mutmut 3.x** because it's actively maintained, has built-in parallelism via `--max-children`, and integrates cleanly with pytest. It doesn't run natively on Windows, so I built a Docker image based on `python:3.11-slim` and ran everything inside it.

## Scoping: where to run mutations

Mutation testing has the highest value where the test:code ratio is high and the logic is non-trivial. For my Django codebase that means `services.py` and `permissions.py` files. It is a poor fit for:

- **Views**, which are mostly orchestration, serializer wiring, and HTTP plumbing. Mutants there often survive for reasons unrelated to test quality.
- **Serializers and models**, which are largely declarative. Most mutations on them are trivial.
- **Migrations**, which run once and never appear in the regression path.

The project has **11 services and 7 permissions files** (18 total). I scoped to all 18 deliberately. Files without unit tests (e.g. `dokumen_monitoring/services.py`, `user_profile/services.py`) were left in scope so the report would surface them as zero-coverage gaps, rather than hide them.

Configuration in `pyproject.toml`:

```toml
[tool.mutmut]
paths_to_mutate = [
    "authentication/services.py",
    "documents/services.py",
    # ... 9 more services
    "pengajuan/permissions.py",
    # ... 6 more permissions
]
also_copy = ["manage.py", "gurubesarmengajar/", "pengajuan/", ]  # plus all Django apps
do_not_mutate_patterns = ['logger\.\w+', 'log\.\w+', 'raise \w+']
pytest_add_cli_args = ["--ds=gurubesarmengajar.settings", "--reuse-db", "-k", "not throttle"]
```

`do_not_mutate_patterns` excludes log statements upfront. Coles et al. (PIT, 2016) recommend skipping log and boilerplate to avoid test theater.

Run:
```bash
docker run --rm -e DATABASE_URL='sqlite:///:memory:' \
  -v "$(pwd):/app" mutmut-gbm mutmut run --max-children 4
```

## Round 1: the baseline

![Round 1 mutmut output](/images/round1-baseline.png)

Two findings jumped out:

**First, 102 mutants survived** across 8 files. The worst was `authentication/services.py` at 48% (39 survivors). Permission files had moderate counts (kegiatan 11, periode 14). The rest were small clusters in `pengajuan`, `notification`, and `statistik_prodi`.

**Second, five files produced *zero* mutants**: `documents/services.py`, `dokumen_monitoring/services.py`, `jenis_kegiatan/services.py`, `laporan/services.py`, `sertifikat/services.py`. About **1024 lines of production code that mutmut effectively did not analyze**. That's not a 100% score, it's a blind spot. Something needed investigation before I trusted the rest of the report.

## Round 2: investigating the N/A anomaly

I checked what the five `N/A` files have in common:

![Static method evidence](/images/round2-staticmethod-evidence.png)

Every one of them uses `@staticmethod` heavily. The control file `pengajuan/services.py` has zero `@staticmethod` and produced 46 mutants normally. To confirm, I checked an old `.mutmut-cache` from mutmut 2.x on the same `sertifikat/services.py`. That one had 19 mutants and 18 of them killed. **mutmut 3.x silently skips `@staticmethod` methods**. This is a known regression from 2.x.

About 1024 LOC of production code is **invisible to my current setup**. My options:

1. Refactor `@staticmethod` to instance methods. Multi-day refactor, touches every caller.
2. Run mutmut 2.x for these files. Adds CI complexity, plus 2.x has its own compat issues.
3. Accept the limitation, document it, lean on regular unit tests. Pragmatic.

I went with option 3 for this sprint, with the addition of a CI rule that requires test updates whenever any of the five files is modified. The gap is acknowledged, not hidden.

## Round 3: killing real survivors on the easiest four files

With the blind spot understood, I prioritized the four files with the smallest survivor counts. Total scope: 21 mutants across 4 files.

| File | Round 1 survivors | Approach |
|---|---|---|
| `pengajuan/permissions.py` | 2 | Specific load-bearing tests |
| `kegiatan/services.py` | 4 | `getattr` fallback path tests |
| `statistik_prodi/services.py` | 7 | Default-value mutants |
| `pengajuan/services.py` | 8 | Parameter-passing checks |

The technique was the same on every file: **write load-bearing tests where each assertion targets one mutation class**, no test theater.

### Example: `pengajuan/permissions.py`

Two survivors, both in `IsKaprodiRoleOnly.has_permission`.

**Survivor #1:** `and` → `or`. An unauthenticated user with a non-null `request.user` would slip through. The test never sent a "user-exists-but-not-authenticated" case to this check.

**Survivor #2:** `getattr(user, 'role', None)` → `getattr(user, 'role', )`. The original returns None silently for users without `role`; the mutant raises `AttributeError`. The test never exercised the missing-attribute case.

Two new tests, each killing one mutant:

```python
def test_user_object_exists_but_not_authenticated_denied(self):
    """Locks `and` from being mutated to `or`."""
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

After re-running: 28/28 mutants killed, **100%**.

### Equivalent mutants

Five of the 21 survivors turned out to be **equivalent mutants**, mutations whose behavior is indistinguishable from the original. They all involve **signature default values**:

```python
order: str = "desc"      # original
order: str = "XXdescXX"  # mutant, never observable
```

mutmut 3.5's trampoline binds the original function's default before forwarding `args=[order]` to the mutant. The mutated default is overwritten before it ever takes effect. Killing these would require contrived assertions that don't reflect product behavior, so I left them alive and documented them in commit messages.

### Final state

![Round 3 results](/images/round3-final.png)

| File | Survived (R1) | Killed | Equivalent | Score |
|---|---|---|---|---|
| pengajuan/permissions.py | 2 | 2 | 0 | **100%** |
| kegiatan/services.py | 4 | 4 | 0 | **100%** |
| statistik_prodi/services.py | 7 | 4 | 3 | 97.3% |
| pengajuan/services.py | 8 | 6 | 2 | 95.7% |
| **Total in scope** | **21** | **16** | **5** | **97.6%** |

10 new tests landed across 3 commits. Four other files (kegiatan/permissions, periode/permissions, notification/services, authentication/services) still have unresolved survivors from Round 1, and they're the next iteration. The decision to defer them was deliberate: tackling heavy fixtures for JWT/email/transactional logic is the kind of work that benefits from breaking out of the survivor-killing flow.

## Measurable impact

| Stage | Survived | Score |
|---|---|---|
| Round 1 (baseline) | 102 | 82.9% |
| Round 2 (anomaly diagnosed, no code change) | 102 | 82.9% |
| Round 3 (load-bearing tests on 4 files) | 86 | 85.6% |

The aggregate score moved modestly. The per-file picture is what matters: two security-critical files are at 100%, two more above 95% with equivalent mutants documented.

## What I learned

**Coverage answers "did the test run?". Mutation testing answers "does the test understand what it's testing?".** The most security-critical file in the project (`authentication/services.py`) sat at 48% even with much higher line coverage.

**Tools have blind spots, and finding them is part of the work.** mutmut 3.x silently removed 1024 LOC of `@staticmethod` code from the analysis. Without Round 2, I would have falsely reported five services as needing no work.

For Django teams: use mutmut, scope it to services and permissions, filter out logger mutations, run it through Docker if you're on Windows, don't gate every PR with it, and **sanity-check files that report zero mutants**.
