+++
title = "Architectural fitness functions: making the rules a CI gate, not a wish"
date = "2026-05-20"
author = "Husin Hidayatul"
description = "After three refactor commits I asked: what stops the cleanup from quietly rotting? The answer was a set of architectural fitness functions in GitLab CI, scoped with diff-scan so the team can still merge."
toc = true
tags = ["fitness-functions", "evolutionary-architecture", "django", "ci-cd", "gitlab", "refactoring"]
categories = ["software-architecture"]
+++

I shipped three small refactor commits on the `pengajuan` and `kegiatan` apps this sprint. Three days later I realized the cleanup is fragile: nothing stops the next teammate, or me on a Friday afternoon, from putting an `.objects.filter(...)` back into a view. Code review is human, and humans get tired.

This post is about the answer I settled on: **architectural fitness functions** running in GitLab CI on every merge request. A refactor without a guardrail is half the job.

![Cover diagram: a refactored module on the left with three small gates labeled "fitness function" between it and a stream of future commits arriving from the right](/images/fitness-cover.png)

## The term, in one paragraph

The phrase **architectural fitness function** comes from Neal Ford and Rebecca Parsons, *Building Evolutionary Architectures* (O'Reilly, 2017), with a foreword by Martin Fowler. The term itself is borrowed from evolutionary computing, where a fitness function scores how close a candidate solution is to ideal. Applied to architecture, it is **an automated, objective check that the system still satisfies a chosen architectural characteristic**. Where unit tests answer *"does the code behave correctly?"*, fitness functions answer *"is the code still organized the way we agreed to organize it?"*.

![Two-panel diagram. Left panel: unit test asking "does the code do the right thing?". Right panel: fitness function asking "is the code organized the right way?". Below each, an example test snippet.](/images/fitness-vs-unit-test.png)

Fowler has been writing about this idea, under different names, for two decades: *Tradeable Quality Attributes*, *Architecturally-Significant Requirements*, the **Reversibility** principle in his "Evolutionary Architecture" essay. Ford and Parsons gave it a single term and an operational definition.

## Why I needed one

Before adding fitness functions, my situation was the classic refactor-rot trap:

| State | What protects it? |
|---|---|
| Commit 1 — extracted `M2MSyncService` to remove duplicated sync logic | Nothing |
| Commit 2 — introduced `PengajuanRepository` so views stop touching the ORM | Nothing |
| Commit 3 — unified error-handling decorator across both apps | Nothing |

A Sonar dashboard would tell me, **eventually**, that smells were creeping back. A dashboard is a report. I wanted a **gate**: a job that fails the pipeline and blocks merge the moment a rule is broken, with a precise message pointing at the offending line.

![Comparison: top row "Sonar dashboard" shows a delayed weekly trend chart with an arrow saying "regression visible 7 days later". Bottom row "Fitness function" shows a red X on a single MR with arrow saying "merge blocked, 30 seconds after push"](/images/fitness-dashboard-vs-gate.png)

## The Ford & Parsons taxonomy, briefly

Fitness functions are not one thing. Ford and Parsons categorize them along several axes; the two that mattered for me:

- **Triggered** (runs on a discrete event like an MR) vs **Continuous** (runs against production traffic, e.g. latency budgets). Mine are all triggered, because that's where CI lives.
- **Atomic** (checks one rule on one piece of code) vs **Holistic** (checks an emergent property across the system, like end-to-end latency). Mine are all atomic, intentionally; emergent rules are powerful but easier to write wrong, and I wanted my first set to be obviously correct.

![A 2x2 grid: rows triggered/continuous, columns atomic/holistic. Each quadrant has one short example. My four rules are clustered in the triggered+atomic quadrant.](/images/fitness-ford-taxonomy.png)

This is a useful frame when defending the design to a reviewer or a lecturer: *"I deliberately chose triggered+atomic for the first generation. Holistic latency fitness comes after the Prometheus stack is mature."*

## The four rules I wrote

All four live in `tests/test_architecture.py`, run by the same pytest the rest of the suite already uses, and depend on nothing beyond Python's `ast` module. Total cost: about 90 lines of test code.

### Rule 1: views must not query the ORM directly

The Repository pattern I introduced in commit 2 only matters if it's the only path. The rule scans `pengajuan/views/` and `kegiatan/views/` and asserts no file contains `.objects.`. Violations name the file and a suggested fix.

```python
def test_views_tidak_query_orm_langsung():
    pelanggaran = []
    for file in _python_files("pengajuan/views"):
        if ".objects." in file.read_text(encoding="utf-8"):
            pelanggaran.append(str(file))
    assert not pelanggaran, (
        f"View masih query ORM langsung: {pelanggaran}. "
        "Pakai PengajuanRepository / PengajuanService."
    )
```

![Pytest output panel: top frame green (state today), bottom frame red after adding a single line Pengajuan.objects.filter(...) to a view, with the exact assertion message visible](/images/fitness-rule1-views.png)

### Rule 2: serializer `get_*` methods must not access the database

Serializer methods that hit the DB cause N+1 in any `many=True` listing. The rule walks each `serializers.py` with `ast`, finds methods starting with `get_`, and checks the unparsed source for `.objects.`.

```python
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name.startswith("get_"):
        if ".objects." in ast.unparse(node):
            pelanggaran.append(f"{file}::{node.name}")
```

![A code snippet from pengajuan/serializers.py showing a get_periode_name method with Periode.objects.get(...), and below it the test output flagging "serializers.py::get_periode_name"](/images/fitness-rule2-serializer.png)

### Rule 3: no function longer than the current budget

*Long Method* is the recurring smell I fight every sprint. This rule walks every `*.py` in the two apps, computes each function's `end_lineno - lineno`, and asserts no function exceeds the budget.

The budget is intentionally loose at the start so existing legacy passes. It **ratchets down** sprint by sprint. The honest narrative I tell my lecturer: *the rule is a slope, not a snapshot*.

![Bar chart of function lengths sorted descending across the two apps. A horizontal line marks the threshold. A few bars cross the line and are highlighted in red; below the chart, the same chart from a future sprint shows the threshold lowered and the longest bar shorter.](/images/fitness-rule3-long-method.png)

### Rule 4: apps must not import each other's internals

`pengajuan` and `kegiatan` can depend on each other's `models.py` (the public contract), but never on each other's `services.py` or `views/`. This protects module boundaries and prevents accidental cyclic coupling.

```python
TERLARANG = (
    "from kegiatan.services", "from kegiatan.views",
    "from pengajuan.services", "from pengajuan.views",
)
```

![A two-app boundary diagram. Green arrows are allowed (model-to-model). Red crossed-out arrows are forbidden (service-to-service or view-to-view across apps).](/images/fitness-rule4-boundaries.png)

## The legacy code wall, and how to step around it

The first time I ran rule 1 against the real codebase, **seven files failed**. Committing that and turning on `allow_failure: false` would turn the entire team's pipeline red until every legacy view was refactored. In the last sprint, that's how you become the enemy.

Standard solutions for this exact problem, ranked from blunt to elegant:

| Strategy | What it does | When it fits |
|---|---|---|
| **Whitelist** | List the legacy files explicitly, ignore them | <5 files, with TODO owner |
| **Threshold/Budget** | Assert `len(violations) <= N`, lower N each sprint | Visible progress, simple, no infra |
| **Ratchet file** | Store the count in tracked JSON, fail if it ever goes up | Long-running projects |
| **Diff-scan** | Only check files actually changed in the current MR | Most fair, matches existing `diff-cover` style |

I picked **diff-scan**. Two reasons.

**One:** it matches a pattern already in our pipeline. The `diff_coverage` job in `.gitlab-ci.yml` uses `diff-cover --fail-under=100` against the MR's target branch. The team's mental model is already there. Same philosophy: don't punish people for legacy they didn't write, but never let them add new debt.

**Two:** it embodies the **boy scout rule** that Robert C. Martin formalized and that Fowler frequently echoes: *leave the code cleaner than you found it*. Not perfect. Cleaner.

![Two-panel illustration. Left "full scan": every legacy violation across the codebase shown in red, overwhelming. Right "diff scan": only the lines changed in the current MR highlighted, with the rest greyed out. A tiny new violation in the diff is flagged.](/images/fitness-diff-vs-full-scan.png)

## GitLab CI integration

The new job sits inside the existing `quality` stage, next to `diff_coverage` and `sonarqube`. No new Docker image. The whole thing adds about 15 seconds to the pipeline.

```yaml
architecture_fitness:
  stage: quality
  image: python:3.11
  rules:
    - if: '$CI_COMMIT_BRANCH =~ /^chore\/mr-analyzer-.+$/ || $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME =~ /^chore\/mr-analyzer-.+$/'
      when: never
    - when: always
  variables:
    GIT_DEPTH: "0"
  before_script:
    - pip install -r requirements.txt
  script:
    - export BASE="${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-staging}"
    - git fetch origin "$BASE"
    - python tests/check_architecture_diff.py --base "origin/$BASE"
  allow_failure: false
```

`allow_failure: false` is the part that matters. A warning-only job gets muted within a week. The whole point is to be a **gate**, not advice.

![GitLab pipeline view, two stacked screenshots: top shows all green including architecture_fitness; bottom shows the same pipeline with architecture_fitness red, expanded to show the precise assertion message](/images/fitness-gitlab-pipeline.png)

## A 90-second demo that lands

The clearest demo to a lecturer is also the simplest:

1. Open the project. Pipeline on `main` is green.
2. Open a fresh MR. Add **one line** to a view: `Pengajuan.objects.filter(status="disetujui")`.
3. Push. Watch the `architecture_fitness` job go red within 30 seconds:
   ```
   AssertionError: View pengajuan masih query ORM langsung:
     pengajuan/views/views_kaprodi.py
   Pakai PengajuanRepository / PengajuanService.
   ```
4. Try to click **Merge**. GitLab blocks it because the pipeline failed.
5. Remove the line. Pipeline green. Merge unblocks.

![Three-frame timeline: frame 1 "MR opened, green", frame 2 "violating commit pushed, red, merge button greyed out with a small lock icon", frame 3 "violation removed, green again, merge button live"](/images/fitness-demo-block-merge.png)

This sequence is the whole thesis compressed: **architecture is a property the team must continuously defend, not a one-time deliverable**.

## What fitness functions are not

Fowler, Ford, and Parsons all warn against treating any pattern as universal. Fitness functions have real liabilities I want to name explicitly, because pretending they don't is what turns a useful tool into theater.

**They are not a substitute for code review.** A fitness function catches mechanical violations. It cannot evaluate a design decision, judge naming, or notice that a clever workaround defeats the rule's spirit. Use fitness functions to **automate the boring checks** so reviewers spend time on judgment.

**They are not a substitute for refactoring.** A rule that gates a codebase before the refactor exists is opinion theater. Every rule in my set sits behind a real refactor commit that made the rule achievable. Rule 1 follows commit 2 (Repository extraction), not the other way around.

**They are not free of false positives.** My rule 1 catches `.objects.` literally. A determined developer can write `OBJ = Pengajuan.objects; OBJ.filter(...)` and slip past. Static analysis raises the cost of regression; it doesn't eliminate it. The team-agreement layer underneath the rule is what really enforces it.

**They are not free for the team.** Each rule is a contract you're asking everyone to honor. Adding a rule without discussion turns a code-quality tool into an interpersonal conflict. I floated each rule on the team channel before flipping `allow_failure: false`. Sometimes the answer was *"good rule, but lower the threshold first"* — that's the rule working as intended.

## What I learned

**A refactor without a guardrail is half the job.** Three clean commits feel productive in the moment, then erode silently a few sprints later. The guardrail is what makes the cleanup worth the time.

**A fitness function is a refactor's seatbelt.** It doesn't make the code better by itself. It keeps the improvement in place while the team keeps moving fast.

**Fowler's "small steps" idea scales to architecture, not just code.** Each fitness function is small, parameterizable, and reversible. Adding one is itself a small, reversible step. The same methodology that produced the three refactor commits produced the four fitness functions on top of them.

For the next iteration I want to tighten rule 3's budget by 10 lines, add a fifth rule about cyclomatic complexity using `radon`, and start trending the violation counts on a tiny Grafana panel so the team sees the slope, not just the snapshot. The goal was never a single perfect commit. The goal is a downward slope, maintained.
