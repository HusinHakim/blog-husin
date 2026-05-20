+++
title = "We all know ORM doesn't belong in views.py. Why does it keep showing up?"
date = "2026-05-20"
author = "Husin Hidayatul"
description = "Knowing a rule and enforcing it are different problems. Three architectural fitness functions, gated in GitLab CI with diff-scan, that turn the rules into pipeline jobs."
toc = true
mermaid = true
tags = ["fitness-functions", "evolutionary-architecture", "django", "ci-cd", "gitlab", "refactoring"]
categories = ["software-architecture"]
+++

We all know putting `.objects.filter(...)` directly inside a Django view is bad practice. Same with hitting the database from inside a serializer's `get_*` method. Same with one app reaching into another app's `services.py`. Every code review repeats it. Every Django tutorial past chapter one says it.

So why does it keep happening in real projects?

Because **knowing a rule is not the same as enforcing one**. Code review is human, humans get tired, deadlines win. Three weeks after I extracted `PengajuanRepository` to clean up exactly this problem, what was stopping the next teammate, or me on a Friday afternoon, from sliding `Pengajuan.objects.filter(...)` back into a view?

The answer I built this sprint: **architectural fitness functions running in GitLab CI**. A short post about three small tests that make the rules un-bypassable.

![Cover: a clean Django view on the left, a small gate labeled "fitness function" in the middle, and a stream of incoming MR commits on the right — only the well-behaved ones pass through](/images/fitness-cover.png)

## What's a fitness function, in one paragraph

Term comes from Neal Ford and Rebecca Parsons, *Building Evolutionary Architectures* (2017), foreword by Martin Fowler. A **fitness function** is an automated test that checks the **shape** of your code, not its behavior. Unit tests ask *"did the function return the right number?"*. Fitness functions ask *"is the function in the right layer?"*. Both run in pytest. Both fail the build when they go red.

{{< mermaid >}}
flowchart LR
    UT[Unit test] -. asks .-> Q1((does the code<br/>do the right thing?))
    FF[Fitness function] -. asks .-> Q2((is the code<br/>in the right place?))
    Q1 --> EX1[assert result == 42]
    Q2 --> EX2[assert '.objects.' not in views]
{{< /mermaid >}}

That's the whole concept. The rest of the post is what they look like in practice, and how I kept them from ruining my team's day.

## The three rules I actually shipped

All three live in one file, `tests/test_architecture.py`. Same pytest runner as the rest of the suite. No new dependencies, no DSL, no magic.

### Rule 1 — Views must not query the ORM

After I extracted `PengajuanRepository`, the only way that pattern keeps working is if the view layer truly never talks to the ORM. So the rule scans `pengajuan/views/` and fails if any file contains `.objects.`.

```python
def test_views_tidak_query_orm_langsung():
    pelanggaran = []
    for file in _python_files("pengajuan/views"):
        if ".objects." in file.read_text(encoding="utf-8"):
            pelanggaran.append(str(file))
    assert not pelanggaran, (
        f"View masih query ORM langsung: {pelanggaran}. "
        "Pakai PengajuanRepository."
    )
```

If somebody adds one line back into a view, the test message tells them exactly which file and what to do instead. No mystery, no "go ask a senior".

![Two pytest output panels: top is green (current state), bottom is red after someone adds Pengajuan.objects.filter(...) to a view, with the assertion message visible](/images/fitness-rule1-views.png)

### Rule 2 — Serializer `get_*` methods must not touch the database

This is the N+1 trap. The moment a serializer field method runs a query, every item in a `many=True` listing triggers a round trip. The rule walks each `serializers.py`, finds methods starting with `get_`, and checks the unparsed body for `.objects.`.

```python
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name.startswith("get_"):
        if ".objects." in ast.unparse(node):
            pelanggaran.append(f"{file}::{node.name}")
```

The fix is always the same: move the lookup to a repository or pass it in via serializer `context`. The rule doesn't dictate which, it just blocks the bad shape.

![Code snippet showing a serializer's get_periode_name method with Periode.objects.get(...), with the test output flagging it underneath](/images/fitness-rule2-serializer.png)

### Rule 3 — Apps must not import each other's internals

`pengajuan` and `kegiatan` are allowed to use each other's **models** (the public contract). They are not allowed to reach into each other's `services.py` or `views/`. This keeps the apps decoupled and prevents accidental cyclic dependencies.

{{< mermaid >}}
flowchart LR
    subgraph PA[pengajuan]
        PM[models.py]
        PS[services.py]
        PV[views/]
    end
    subgraph KA[kegiatan]
        KM[models.py]
        KS[services.py]
        KV[views/]
    end
    PV --> PM
    PV --> PS
    KV --> KM
    KV --> KS
    PS -. ok .-> KM
    KS -. ok .-> PM
    PV -- FORBIDDEN --x KS
    KV -- FORBIDDEN --x PS
{{< /mermaid >}}

```python
TERLARANG = (
    "from kegiatan.services", "from kegiatan.views",
    "from pengajuan.services", "from pengajuan.views",
)
```

## How a single rule actually runs

If you've never written one before, here's the whole mechanic. Same five steps for all three rules.

{{< mermaid >}}
flowchart TB
    Start([pytest tests/test_architecture.py]) --> Collect[Collect target files]
    Collect --> Loop{For each file}
    Loop --> Check{Rule violated?<br/>e.g. contains .objects.}
    Check -- yes --> Record[Save file:line]
    Check -- no --> Loop
    Record --> Loop
    Loop -- done --> Assert{Any violations?}
    Assert -- none --> Green([Green, merge allowed])
    Assert -- some --> Red([Red, merge blocked])
    style Green fill:#c8e6c9,stroke:#388e3c
    style Red fill:#ffcdd2,stroke:#c62828
{{< /mermaid >}}

**Collect, loop, check, assert.** The whole pattern fits in 15 lines of Python per rule. A teammate can read one of these tests in 60 seconds and have an opinion. That readability is the point — if people can't understand the rule, they won't respect it.

## The legacy code wall

The first time I ran rule 1, **seven files failed**. If I had merged that with `allow_failure: false`, the entire team's pipeline would have been red until every legacy view was rewritten. In the last sprint, that's how you become the most unpopular person in the channel.

There are four common ways out:

| Strategy | What it does | Trade-off |
|---|---|---|
| Whitelist | List legacy files, ignore them | Lazy, gets forgotten |
| Threshold | `assert violations <= 7`, lower N each sprint | Visible slope, manual |
| Ratchet file | Auto-saved count, fails if it ever goes up | More infra |
| **Diff-scan** | Only check files touched by this MR | Fair, matches `diff-cover` |

I picked **diff-scan**. Two reasons. First, the team already understands this pattern from the `diff_coverage` job in the pipeline. Same mental model. Second, it matches the **boy scout rule** Fowler keeps repeating: *leave the code cleaner than you found it*. Not perfect. Cleaner.

{{< mermaid >}}
flowchart TB
    Push([Push to MR branch]) --> Fetch[Fetch target branch]
    Fetch --> Diff[List changed .py files]
    Diff --> Any{Any in scope?}
    Any -- no --> Skip([Skip, green])
    Any -- yes --> Run[Run rules on changed files only]
    Run --> Ok{Violations?}
    Ok -- none --> Pass([Green, merge allowed])
    Ok -- some --> Fail([Red, merge blocked<br/>with file:line message])
    style Pass fill:#c8e6c9,stroke:#388e3c
    style Skip fill:#c8e6c9,stroke:#388e3c
    style Fail fill:#ffcdd2,stroke:#c62828
{{< /mermaid >}}

A legacy file nobody touched in this MR is **never opened**. Only new debt added by this MR can fail the build. That's the rule that makes the gate humane.

## Wiring it into GitLab CI

The new job sits in the existing `quality` stage, right next to `diff_coverage` and `sonarqube`. No new image. About 15 extra seconds per pipeline.

{{< mermaid >}}
flowchart LR
    Push([git push]) --> Lint --> Test --> Q1[diff_coverage]
    Test --> Q2[sonarqube]
    Test --> Q3[architecture_fitness<br/>NEW]
    Q1 --> Build --> Deploy
    Q2 --> Build
    Q3 --> Build
    classDef new fill:#fff59d,stroke:#f9a825,stroke-width:2px
    class Q3 new
{{< /mermaid >}}

```yaml
architecture_fitness:
  stage: quality
  image: python:3.11
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

The `allow_failure: false` line is the whole point. A warning-only job gets muted in a week. **A gate that doesn't block is just a sticker.**

![GitLab pipeline view, two stacked screenshots: top all green including architecture_fitness, bottom with architecture_fitness red and the assertion message expanded](/images/fitness-gitlab-pipeline.png)

## The demo I'd run for a reviewer

Ninety seconds. No slides needed.

{{< mermaid >}}
sequenceDiagram
    autonumber
    actor Dev
    participant Git as GitLab
    participant CI

    Dev->>Git: Open MR, clean
    Git->>CI: Run pipeline
    CI-->>Git: All green
    Dev->>Git: Add Pengajuan.objects.filter(...) to a view
    Git->>CI: Re-run pipeline
    CI->>CI: architecture_fitness checks the diff
    CI-->>Git: Red, file:line message
    Git-->>Dev: Merge button disabled
    Dev->>Git: Revert that line
    Git->>CI: Re-run pipeline
    CI-->>Git: Green again
    Git-->>Dev: Merge re-enabled
{{< /mermaid >}}

That sequence is the whole post in one minute: **the rule is real, the rule is enforced, the rule is humane to fix.**

## Where this falls short, honestly

I want to name the limits, because a tool that gets oversold ends up distrusted.

**Static checks have escape hatches.** Rule 1 looks for `.objects.`. A determined developer can write `OBJ = Pengajuan.objects; OBJ.filter(...)` and slip past. Fitness functions raise the cost of regression. They don't eliminate it. The team's agreement underneath the rule is what really enforces anything.

**A rule without a refactor behind it is theater.** I only added rule 1 *after* I extracted `PengajuanRepository`. If I had added the rule first, the team would have been blocked indefinitely with no fix path. **Refactor first, then gate.**

**Code review still matters.** A fitness function catches mechanical violations. It can't tell you a name is wrong, an abstraction is leaky, or a clever workaround defeats the rule's spirit. Use fitness functions to **automate the boring checks** so reviewers can spend their attention on judgment.

## What I'd tell my past self

Three small lessons I wish I'd internalized earlier in the sprint:

**Knowing a rule is the easy half.** Everyone on the team already knew ORM didn't belong in views. The rule was never the gap. Enforcement was.

**A fitness function is a refactor's seatbelt.** It doesn't make the code better. It keeps the improvement from quietly rotting back.

**Small and reversible beats grand and unmovable.** Each rule is 15 lines. Adding one is a 30-minute commit. Removing one is the same. The methodology that produced the rules is the same methodology Fowler describes for refactoring itself — small steps, green tests between each, easy to undo.

Next iteration I want to add a fourth rule about raw SQL outside repositories, and start trending the violation counts on a small Grafana panel so the slope is visible. The goal was never a perfect single commit. The goal is a slope, maintained.
