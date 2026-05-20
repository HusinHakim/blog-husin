+++
title = "Refactoring is not enough: I added architectural fitness functions so the cleanup actually sticks"
date = "2026-05-20"
author = "Husin Hidayatul"
description = "Three Fowler-named refactorings on the pengajuan and kegiatan apps, plus a diff-scan fitness function job in GitLab CI that prevents the cleanup from quietly rotting back into the codebase."
toc = true
tags = ["refactoring", "design-patterns", "fitness-functions", "django", "ci-cd", "architecture"]
categories = ["software-architecture"]
+++

I refactored three pieces of the `pengajuan` and `kegiatan` apps this sprint. After the third commit landed, a question got stuck in my head: **what stops the next teammate, or me three weeks from now, from putting all of this back the way it was?** A clean diff today is worthless if the same smell quietly grows back into the codebase tomorrow.

This post is about the answer: a small set of **architectural fitness functions** running in GitLab CI on every merge request. Refactor once, then put a guardrail behind it.

![Cover diagram: a refactored module with a small gate labeled "fitness function" between it and an arrow representing future commits](/images/refactoring-cover.png)

## Where the rubric pointed me

The PPL rubric for this sprint asked specifically for **Martin Fowler refactoring** plus **common enterprise organization patterns** like DTO, Service layer, and Repository layer. Fowler's two relevant books map almost one-to-one onto those phrases: *Refactoring* (1999, 2nd ed. 2018) for the named refactorings and code smells, *Patterns of Enterprise Application Architecture* (2002) for the layer patterns. Together they describe how to fix structural problems and how to prevent them.

The rubric also asked for **diff commits**. Fowler is opinionated about this. Refactoring in his methodology means **small, behavior-preserving steps with tests staying green between each one**. A single 2000-line "refactor PR" is not Fowler-style. Three focused commits are.

![Two-column slide: left "what the rubric asks", right "what Fowler's two books actually deliver", with arrows mapping between them](/images/refactoring-rubric-map.png)

## What I refactored, in three commits

I picked three smells that recurred in `pengajuan/` and `kegiatan/` and that each map to a different named refactoring. Each commit is small, ships green tests, and can be reverted independently.

### Commit 1: Extract M2MSyncService — remove Duplicated Code across three services

`kegiatan/services.py` had three sibling services (`KegiatanKaprodiService`, `KegiatanGuruBesarService`, `KegiatanJenisKegiatanService`) whose `sync_*` methods were structurally identical. The same diff-existing-vs-new, delete-missing, bulk-create-new pattern was repeated three times with only the FK field name changing.

> **Smell:** *Duplicate Code* (Fowler ch.3)
> **Refactoring:** *Form Template Method* + *Parameterize Function*
> **Rule of Three** is satisfied: the pattern appears three times. Fowler's heuristic says wait for the third occurrence before extracting, to avoid premature abstraction.

![Side-by-side diff: three identical sync methods on the left, one parameterized M2MSyncService on the right](/images/refactor-commit1-m2m-sync.png)

### Commit 2: Introduce PengajuanRepository — separate persistence from views and serializers

`AdminPengajuanSerializer._get_related_lookup_value` fell back to `model.objects.filter(...)` inside the serializer. `views_admin._build_lookup_maps` queried three tables directly. `views_kaprodi.ajukan_guru_besar` did its own ORM calls. The ORM was leaking across every layer.

> **Smell:** *Feature Envy* + *Inappropriate Intimacy* (views and serializers knowing too much about the database)
> **Pattern (Fowler, PoEAA):** *Repository* — abstract the collection of domain objects so the consumer just asks for them by intent (`for_admin_listing(ids)`), not by query syntax.

![Layered diagram: views and serializers no longer reach down to ORM, both go through PengajuanRepository, which is the only layer that touches Django ORM](/images/refactor-commit2-repository-layer.png)

### Commit 3: Unify error-handling decorator across pengajuan and kegiatan

`pengajuan/` used a clean decorator `@handle_pengajuan_service_exceptions` (`pengajuan/decorators.py:42`). `kegiatan/` used a closure helper `_execute_with_standard_error_handling` that wrapped each handler. Two different mental models for the same cross-cutting concern.

> **Smell:** *Shotgun Surgery* (any change to error policy required edits in two places, two styles)
> **Refactoring:** *Substitute Algorithm* + *Move Function*. The kegiatan helper becomes a decorator with the same shape as pengajuan's. Both apps now share one mental model for HTTP error mapping plus Prometheus metric emission.

![Before/after of a kegiatan view: closure-wrapped handler becomes a flat handler with a decorator on top](/images/refactor-commit3-decorator-unification.png)

Three commits, three different refactoring names, three different smells. That covers the **Level 3 rubric** requirement of *minimum three different cases with diff commit*.

## The question I had after commit 3

Code review is human. People get tired. The same smell I just removed can come back in two weeks, and nobody is going to scan 3000 lines per MR to catch it.

I have a Sonar job in the pipeline already. Sonar reports on the whole codebase, with a delay. It's a dashboard, not a gate. What I wanted was **a gate**: a CI job that fails the merge if a specific architectural rule is broken.

This is the idea of a **fitness function**.

## Architectural fitness functions, in one paragraph

The term comes from Neal Ford and Rebecca Parsons, *Building Evolutionary Architectures* (2017), with a foreword written by Martin Fowler. The term itself is borrowed from evolutionary computing, where a fitness function scores how close a solution is to ideal. Applied to software architecture, a fitness function is **an automated test that checks whether your codebase still satisfies a chosen architectural constraint**. Unit tests check behavior; fitness functions check structure.

![Side-by-side diagram: unit test asks "does the code do the right thing?" while fitness function asks "is the code organized the right way?"](/images/fitness-function-vs-unit-test.png)

## The four fitness functions I added

All four live in `tests/test_architecture.py`, run by the same pytest the rest of the suite uses, and require no extra dependencies beyond Python's `ast` module.

### 1. Views must not query the ORM directly

After commit 2, the rule is: views call the Repository, never `.objects.`. The fitness function scans `pengajuan/views/` and `kegiatan/views/` and fails if any file contains a direct ORM call.

![Pytest output showing the test green, then a diff that adds Pengajuan.objects.get(...) to a view, then the same test red with a precise file:line message](/images/fitness-1-views-no-orm.png)

### 2. Serializer `get_*` methods must not access the database

Serializer methods that hit the DB cause N+1 in `many=True` listings. The function walks each `serializers.py` with `ast`, finds methods starting with `get_`, and asserts `.objects.` does not appear in the unparsed source.

![Highlighted section of pengajuan/serializers.py with a get_periode_name method, and a fitness function output flagging the line](/images/fitness-2-serializer-no-db.png)

### 3. No method longer than N lines

`Long Method` is the smell I keep fighting. This function parses every `*.py` in the two apps with `ast`, computes `end_lineno - lineno` per function, and asserts no function exceeds the budget. The threshold starts loose (so the legacy code passes) and ratchets down over sprints.

![Bar chart of function lengths sorted descending, with a horizontal threshold line, and the few bars poking above the line highlighted in red](/images/fitness-3-long-method-budget.png)

### 4. Apps must not import each other's internals

`pengajuan` and `kegiatan` can depend on each other's models (public contract), but never on each other's `services.py` or `views/`. This protects module boundaries and prevents cyclic coupling.

![Module diagram: green arrows for allowed dependencies (model imports), red arrows crossed out for forbidden dependencies (service-to-service across apps)](/images/fitness-4-module-boundaries.png)

## The legacy code wall, and how to step around it

The first time I ran the strict version of rule 1 against the real codebase, **seven files failed**. If I committed that and merged, the entire team's pipeline would be red until every legacy view was refactored. Sprint last sprint, this would have made me the enemy.

Standard solutions, ranked from blunt to elegant:

1. **Whitelist** the seven files explicitly with a TODO comment. Works, gets ignored, accumulates.
2. **Threshold/budget**: assert `len(violations) <= 7` and ratchet the number down each sprint. Visible progress.
3. **Ratchet file**: store the count in a tracked JSON, fail if it ever goes up. Effective but heavier setup.
4. **Diff-scan**: only check the files actually changed in the current MR. Pre-existing legacy is invisible. **Only new violations fail.**

I went with diff-scan, because it matches a pattern already in our pipeline. The `diff_coverage` job in `.gitlab-ci.yml` uses `diff-cover --fail-under=100` against the MR's target branch. Same philosophy: don't punish people for legacy they didn't write, but don't let them add new debt either.

![Two-panel diagram: left panel "full scan" highlights every legacy violation in red, right panel "diff scan" highlights only the lines changed in this MR](/images/fitness-diff-scan-vs-full-scan.png)

This is the *boy scout rule* from Robert C. Martin (often quoted by Fowler): **leave the code cleaner than you found it**. Not perfect, just cleaner.

## GitLab CI integration

The new job sits in the existing `quality` stage, right next to `diff_coverage` and `sonarqube`. It needs no new image and adds about 15 seconds to the pipeline.

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

`allow_failure: false` is deliberate. A warning-only job gets ignored within a week.

![GitLab pipeline view with the architecture_fitness job sitting between diff_coverage and sonarqube, both states shown: all green on top, fitness function red on the bottom with a click-through to the precise failing assertion](/images/fitness-gitlab-pipeline.png)

## A live demo I can show to the dosen

The most convincing demo is also the simplest:

1. Open the project. Pipeline green on `main`.
2. Open a fresh MR. Add one line to a view: `Pengajuan.objects.filter(status="disetujui")`.
3. Push. Watch the `architecture_fitness` job go red with this output:
   ```
   AssertionError: View pengajuan masih query ORM langsung:
     pengajuan/views/views_kaprodi.py
   Pakai PengajuanRepository / PengajuanService.
   ```
4. Try to click Merge. GitLab blocks it because the pipeline failed.
5. Remove the line. Pipeline green again. Merge unblocks.

![Three-panel timeline screenshot: MR opened green, MR with violating commit red with the blocking merge button greyed out, MR fixed and mergeable again](/images/fitness-demo-block-merge.png)

That sequence is the entire Fowler thesis compressed into 90 seconds: **refactoring is not a one-time cleanup, it is an ongoing capability the team needs infrastructure for.**

## Where fitness functions hurt, and when not to use them

Fowler and Ford both warn against treating any pattern as universal. Fitness functions have real liabilities:

- **Premature rules become noise.** If you write a rule before there is a real refactor protecting it, the rule is opinion theater. Each fitness function in my set followed an actual refactoring commit.
- **Strictness must match team agreement.** Adding `allow_failure: false` without discussion turns a code quality tool into an interpersonal conflict. I floated each rule on the team channel before flipping the gate.
- **Static analysis has blind spots.** My rule 1 catches `.objects.` but not a creative aliased import. A determined developer can always work around a rule. Fitness functions raise the cost of regression; they don't eliminate it.
- **Legacy whitelisting can become permanent.** Diff-scan dodges this, but if you ever add a manual whitelist, put a TODO with an owner and a date.

The Fowler-style version of this answer: **rules exist to serve the code, not the other way around**. A rule that consistently blocks legitimate work is a wrong rule. Remove it or rewrite it.

## What I learned

**Refactoring without a guardrail is half the job.** Three clean commits feel productive in the moment, then erode silently in the next sprint when someone, under deadline pressure, takes the shortcut you just removed.

**A fitness function is a refactoring's seatbelt.** It does not improve the code by itself. It keeps the improvement in place while everyone keeps moving fast.

**Fowler's "small steps" idea scales to architecture, not just code.** Each fitness function is small, parameterizable, and reversible. Adding one is itself a small, reversible step. The same methodology that produced the three refactoring commits produced the four fitness functions on top of them.

For the next sprint I want to tighten rule 3's threshold by 10 lines, add a fifth rule about cyclomatic complexity using `radon`, and start trending the rule-3 violation count on a small Grafana panel so the team sees the slope. The improvement was never a single PR. It is a slope.
