+++
title = "Mutation testing with mutmut: when 100% coverage doesn't guarantee good tests"
date = "2026-05-10"
author = "Husin Hidayatul"
description = "Setting up mutation testing on a Django backend with mutmut, what 35 surviving mutants told me about my test suite, and how to spot a real test gap from noise."
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

## How I set it up

The high-level flow is:

1. **Install mutmut** in the project's Python environment (`pip install mutmut`).
2. **Configure `pyproject.toml`** with three things: which files to mutate, which test files to run against the mutants, and any patterns mutmut should ignore.
3. **Run** `mutmut run` from the project root. mutmut generates a `mutants/` directory with mutated copies of source code and runs pytest against each mutant.
4. **Inspect results** with `mutmut results` or by parsing the per-file `.meta` output.

A few practical notes from my setup:

- **mutmut 3.x doesn't run natively on Windows**. I built a small Docker image based on `python:3.11-slim` and ran mutmut inside it. mounting the project root as a volume.
- **The default test database (a remote PostgreSQL) was painfully slow**. I overrode `DATABASE_URL` at container startup to use SQLite in-memory. Smoke test went from 222 seconds to 7 seconds for the same 44 tests.
- **mutmut copies mutated files into `mutants/`** and runs pytest from there. By default it doesn't copy the rest of the project, so Python imports may resolve to the original files instead of the mutated copies. The fix is to list every Django app in the `also_copy` directive so the `mutants/` folder is self-contained.

A minimal `pyproject.toml` for our project ended up looking like this:

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
do_not_mutate_patterns = [
    'logger\.\w+',
    'log\.\w+',
    'raise \w+',
]
pytest_add_cli_args = [
    "--ds=gurubesarmengajar.settings",
    "--reuse-db",
    "-k", "not throttle",
]
pytest_add_cli_args_test_selection = [
    "pengajuan/tests/test_permissions.py",
    "pengajuan/tests/test_services.py",
    "kegiatan/tests/test_permissions.py",
    "periode/tests/test_permissions.py",
    "sertifikat/tests/test_services.py",
]
```

To run it (with the Docker image I built):

```bash
docker run --rm \
  -e DATABASE_URL='sqlite:///:memory:' \
  -v "$(pwd):/app" \
  mutmut-gbm \
  mutmut run --max-children 4
```

After the run finishes, `mutmut results` shows the breakdown.

## Why I scoped to only a few files

A naive first instinct is to mutate the entire codebase. I learned the hard way that this is a bad use of compute and attention.

Mutation testing makes sense where the value is highest: **business logic with high test density**. For our codebase that means `services.py` and `permissions.py` files. It is a poor fit for:

- **Django views**, which are mostly orchestration, serializer wiring, and HTTP plumbing. Mutants there often survive for reasons unrelated to test quality (e.g. mutating a serializer kwarg that no test asserts on).
- **Serializers and models**, which are largely declarative. Most mutations there are trivial or untestable.
- **Migrations**, which run once and are not in the regression path.
- **`@staticmethod` methods**, which mutmut 3.x silently skips. I learned this when one of my services produced **0 mutants**, even though it had 99 lines of real logic. The methods were all decorated with `@staticmethod`. This was a regression from mutmut 2.x.

I scoped to **2 services + 3 permission files**, totaling 299 lines of code. mutmut produced 159 mutants, the run finished in under an hour, and every surviving mutant was either an actionable test gap or an obvious noise pattern.

For a long-running project, my recommendation is to start with **the most security-sensitive logic-heavy files**, not by trying to maximize coverage of LOC.

## Results and analysis

| File | Mutants | Killed | Survived | Score |
|---|---|---|---|---|
| pengajuan/permissions.py | 28 | 26 | 2 | 92.9% |
| pengajuan/services.py | 46 | 38 | 8 | 82.6% |
| kegiatan/permissions.py | 43 | 32 | 11 | 74.4% |
| periode/permissions.py | 42 | 28 | 14 | 66.7% |
| sertifikat/services.py | 0 | - | - | N/A |
| **Total** | **159** | **124** | **35** | **78.0%** |

I categorized the 35 surviving mutants into three groups.

**(a) Real test gaps, security-relevant.** 2 mutants in `pengajuan/permissions.py` were genuine gaps. For example:

```python
# Original
is_logged_in = bool(request.user and request.user.is_authenticated)
# Mutant SURVIVED
is_logged_in = bool(request.user or request.user.is_authenticated)
```

`and` became `or` and the test couldn't tell the difference. This is a permission class for Kaprodi access. If that mutant ever shipped, there would be a real privilege escalation risk.

**(b) Parameter passing not verified.** 8 mutants in `pengajuan/services.py` survived because tests only check the return value, not the internal state. For example, `self.notifier = notifier` mutated to `self.notifier = None` slipped through because no test asserted `service.notifier is the_passed_in_notifier`.

**(c) Logger string noise.** 23 mutants, the largest group, were variations on log messages:

```python
# Original
logger.warning("Unauthorized access attempt to %s", path)
# Mutant SURVIVED
logger.warning("XXUnauthorized access attempt to %sXX", path)
```

Behavior is unchanged. The risk here is observability, not correctness — log searches and monitoring alerts may break if the message is mutated, but the system continues to work. Practical mutation testing usually filters these out via `do_not_mutate_patterns`.

## What I changed

**Two new tests for the Kaprodi permission**, designed to kill the real-gap mutants:

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

**Logger noise filter** in `pyproject.toml`:

```toml
do_not_mutate_patterns = [
    'logger\.\w+',
    'log\.\w+',
    'raise \w+',
]
```

Future runs no longer mutate logger or raise statements, so the score reflects pure logic quality rather than incidental string changes.

## Best practices from the literature

Petrović & Ivanković (Google, 2018) don't set a fixed mutation score threshold, but instead monitor it as a trend and run the analysis as a **nightly job, not per-PR**. The reason is computational cost — DeMillo et al. (1978) noted this problem in the very first mutation testing paper.

Coles et al. (2016, the PIT paper for Java) recommend skipping getters, setters, log statements, and boilerplate. Focus on business logic. I implemented this through `do_not_mutate_patterns` and by scoping `paths_to_mutate` to services and permissions only, never to views.

For the threshold, Boucher et al. (ICST 2017) note that **60-80% mutation score is an acceptable industry baseline**, and **>85% is excellent for critical modules**. My current 78% includes the logger noise; once filtered, the realistic number for pure logic is around 90%, which I'm satisfied with.

## What I learned

Three takeaways:

**Coverage answers "did the test run?". Mutation testing answers "does the test understand what it's testing?".** Two different questions. I thought my tests were strong because of high coverage. mutmut showed me that two security-critical logic paths in the permission classes were not actually exercised in a meaningful way.

**Tools have blind spots, don't treat any single one as ground truth.** mutmut 3.x silently skips `@staticmethod` methods. If I hadn't investigated the "0 mutants" anomaly, I would have falsely reported the sertifikat service as fully tested. Mutation testing is one lens, not the absolute measure.

**The setup pain is part of the lesson.** I hit four blockers (Windows not supported, slow remote DB, flaky throttle test, coverage detection silently broken) and each taught me something about how modern tools assume things that aren't always true. The fixes were all available, but they required patient diagnosis.

For Django teams: use mutmut, scope it to services and permissions (not views), filter out logger mutations in config, run it through Docker if you're on Windows, and don't gate every PR with it.
