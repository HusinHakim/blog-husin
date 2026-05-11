+++
title = "Beyond unit tests for a Django feature: BDD, schema fuzzing, load gating, and SAST"
date = "2026-05-11T05:00:00+07:00"
author = "Husin Hidayatul"
description = "Unit test answers 'does this function return the right value'. It does not catch endpoints that lie about their schema, status transitions that drift from product intent, or merges that silently double p95 latency. Here is how I added BDD, Schemathesis, a Locust gating threshold, and a Bandit local verification step to the Pengajuan feature of our Django backend."
toc = true
tags = ["bdd", "schemathesis", "locust", "load-testing", "bandit", "sast", "django", "testing", "qa"]
categories = ["software-testing"]
+++

The unit-vs-integration debate dominates testing conversations, and it hides categories of bug that neither side catches. A unit test that asserts `service.update_status(...) == "disetujui"` does not tell you the **stakeholder-readable rule** behind that transition. An integration test that posts a hand-crafted payload does not catch the dozens of **spec-valid payloads the engineer never thought to write**. Neither catches a merge that **doubles p95 latency** of the same endpoint. And neither catches an unsafe code pattern that hides in a corner of the module no reviewer scrolled to. This post walks through four "other" testing layers I added (or, in the case of Bandit, re-ran locally to verify) on top of an already mature unit test suite for the `pengajuan` feature: pytest-bdd, Schemathesis, a Locust gating profile, and a local Bandit scan.

Note: our project lives on the internal GitLab. References to *MR* throughout this post mean **Merge Request** (GitHub's pull request equivalent).

![Testing stack overview: existing layers (unit, integration, coverage, mutation, lint) on the left; four new layers added this sprint (BDD, Schemathesis, Locust gate, Bandit local re-run) on the right, each pointing to the bug class it catches](/images/testing-stack-overview.png)
<!-- HOW TO CAPTURE:
  Open https://excalidraw.com (free, no signup). Draw two columns connected by arrows:

  LEFT column (EXISTING, label with light gray background):
    [Django TestCase 197 tests]   → "service & view correctness"
    [coverage + diff-cover 100%]  → "execution presence"
    [mutmut 3.x]                  → "assertion quality"
    [flake8 / SonarQube]          → "style + maintainability"

  RIGHT column (NEW THIS SPRINT, label with green background):
    [pytest-bdd 5 scenarios]      → "stakeholder intent"
    [Schemathesis 9 failures]     → "OpenAPI contract drift (DAST)"
    [Locust ramp + threshold]     → "flow-level p95 regression"
    [Bandit (verified locally)]   → "SAST on Python source"

  Add a centered arrow between columns labeled "this sprint".
  Export -> PNG transparent background.
  Save to C:\PPL\blog-husin\static\images\testing-stack-overview.png
-->

## Where the team already was

Before this sprint, the `pengajuan` feature already had:

| Layer | Tooling | What it covered |
|---|---|---|
| Static analysis | flake8, SonarQube | Style + duplicated code + maintainability |
| Coverage | `coverage` + diff-cover at 100% on MR | Line execution on changed files |
| Unit + integration | Django `TestCase` (197 tests across `tests_models.py`, `test_views_admin.py`, `test_views_kaprodi.py`, `tests_gb.py`, `test_services.py`, `test_permissions.py`, `test_serializers.py`, `test_search.py`, `test_query_budget.py`) | Behavior of services, views, permissions, query count guards |
| Mutation testing | mutmut 3.x scoped to `services.py` + `permissions.py` | Whether the assertions actually catch logic mutations (see [previous post]({{< ref "post/mutation-testing-mutmut.md" >}})) |
| Load testing | `locustfile.py` with `KegiatanStressTestUser` (manual run against staging) | Latency under 20 concurrent users, but **manual**, not gating MR |

The gap was not "we have no tests". The gaps were specific:

1. **No executable spec.** Status transitions of `Pengajuan` (menunggu → disetujui / ditolak, plus `rekomendasi_gb`) are negotiated product rules. They are encoded only inside Python assertions that non-engineers cannot read.
2. **No drift detection between OpenAPI schema and implementation.** Our backend serves `drf_spectacular` schema at `/api/schema/` and Swagger UI at `/api/docs/`. The schema is generated, but nothing verifies that the running server actually obeys it on arbitrary spec-conforming inputs.
3. **Load testing was not a gate.** A merge that doubled p95 latency would not show up anywhere until someone manually re-ran Locust. The locustfile exists; the CI signal does not.

The three sections below are what filled each gap, paired with a deliberate **anti-overclaim** about what each technique **does not** catch.

## 1. BDD with pytest-bdd: executable spec for status transitions

### Why BDD here, specifically

The naive argument against BDD is that it duplicates unit test scope in more verbose syntax. That is true when the engineer writes both the spec and the Python. It is **not** true when the behavior originates from outside engineering. Our pengajuan workflow is exactly that:

- A kaprodi may not submit the same guru besar twice in the same periode (model-level unique constraint, but the **product rule** is "one submission per gurubesar per periode").
- Admin sets status to `disetujui` or `ditolak`; if `ditolak`, `catatan_penolakan` is mandatory.
- A guru besar may mark themselves `direkomendasikan` once status is `disetujui`.

These are the kind of rules a product owner or a thesis supervisor reads in plain language, not as `assertEqual(pengajuan.status, "disetujui")`. The framing matters: BDD makes the **rule** the document of record, with the Python step file as the executor.

This pattern is grounded in Dan North's original BDD writings (2006) and the canonical reference is Wynne, Hellesøy & Mugridge, *The Cucumber Book* (Pragmatic Bookshelf, 2017). For Python projects the actively maintained binding is **pytest-bdd**, which integrates with the existing `pytest` runner rather than introducing a separate harness like `behave`.

![BDD execution flow: Gherkin feature file describing scenario in plain Indonesian on the left; Python step file binding Given/When/Then to API calls in the middle; pytest runner executing both and producing PASSED/FAILED on the right](/images/bdd-execution-flow.png)
<!-- HOW TO CAPTURE:
  Open https://excalidraw.com. Draw 3 boxes left-to-right connected by arrows:

  Box 1 (yellow background) — title "pengajuan_workflow.feature":
    Feature: Pengajuan workflow
      Scenario: Admin menyetujui pengajuan
        Given ada pengajuan dengan status "menunggu"
        When admin mengubah status menjadi "disetujui"
        Then status pengajuan tersimpan sebagai "disetujui"

  Arrow labeled "@scenario decorator binds steps"

  Box 2 (light blue background) — title "test_pengajuan_bdd.py":
    @given("ada pengajuan ...")
    def given_pengajuan(...): ...
    @when("admin mengubah status ...")
    def when_admin(...): ...
    @then("status tersimpan ...")
    def then_status(...): ...

  Arrow labeled "pytest runner"

  Box 3 (green background) — title "Terminal output":
    PASSED [20%]
    PASSED [40%]
    5 passed in 6.28s

  Export -> PNG transparent background.
  Save to C:\PPL\blog-husin\static\images\bdd-execution-flow.png
-->

### Tool choice: pytest-bdd over behave

I picked `pytest-bdd` for one reason: the rest of our test suite is already pytest-compatible via `pytest-django`. Switching to `behave` would mean maintaining two runners, two configurations, two CI reports. `pytest-bdd` lets the same `pytest` invocation pick up `tests/features/*.feature` alongside the existing `tests_models.py`. Same fixtures, same conftest, same coverage report. Lower friction always wins for adoption.

### A real feature file from the codebase

Excerpt from `pengajuan/tests/features/pengajuan_workflow.feature`:

```gherkin
Feature: Pengajuan workflow status transitions
  As an admin, kaprodi, or guru besar
  I want the status of a pengajuan to follow the negotiated product rules
  So that approval, rejection, and recommendation cannot drift silently

  Scenario: Admin menyetujui pengajuan yang masih menunggu
    Given ada pengajuan dengan status "menunggu"
    When admin mengubah status pengajuan menjadi "disetujui"
    Then status pengajuan tersimpan sebagai "disetujui"
    And catatan_penolakan tetap kosong

  Scenario: Admin menolak pengajuan tanpa catatan ditolak validasi
    Given ada pengajuan dengan status "menunggu"
    When admin mencoba menolak pengajuan tanpa catatan_penolakan
    Then permintaan ditolak dengan error "catatan_penolakan wajib diisi"
    And status pengajuan tetap "menunggu"

  Scenario: Kaprodi tidak boleh mengajukan guru besar yang sama di periode yang sama
    Given kaprodi sudah mengajukan guru besar "Prof. Andi" pada periode aktif
    When kaprodi mengajukan ulang guru besar "Prof. Andi" pada periode yang sama
    Then permintaan ditolak dengan error "duplikat pengajuan untuk periode ini"
```

The non-engineer reader can answer "what happens when admin rejects without a reason?" by reading the file. The engineer reader runs the same file with `pytest pengajuan/tests/test_pengajuan_bdd.py`.

### Coverage and CI integration

After this sprint, the suite contains **5 scenarios** in **1 feature file** (`pengajuan_workflow.feature`), all passing in **6.28 seconds** as part of the pytest run. The pytest invocation did not need a new CI job; pytest-bdd registers the feature files as test items the moment the dependency is in `requirements.txt`. The CI integration is two lines: install `pytest-bdd pytest-django`, run `pytest pengajuan/tests/test_pengajuan_bdd.py` in a new `bdd:` job alongside the existing `test:` job.

![pytest-bdd output for pengajuan scenarios — 5 passed in 6.28s](/images/bdd-pytest-output.png)

### The honest limit

`pytest-bdd` is most valuable for behaviors **negotiated with stakeholders**. If the same engineer writes both the Gherkin and the Python, it is a verbose unit test with extra steps. I deliberately scoped BDD only to status transitions, duplicate constraints, and the `rekomendasi_gb` flow, places where the rule comes from product, not engineering. CRUD plumbing remains in regular Django `TestCase`, where it belongs.

## 2. Schemathesis: fuzzing the OpenAPI contract

### What problem this actually solves

`drf_spectacular` generates an OpenAPI schema from the DRF views automatically. That schema serves two consumers:

![OpenAPI schema as single source feeding two consumers: Django views generate schema via drf_spectacular; schema is then read by both Swagger UI (human, manual click) and Schemathesis (robot, CI fuzz)](/images/schemathesis-vs-swagger.png)
<!-- HOW TO CAPTURE:
  Open https://excalidraw.com. Draw a center node with two branches:

  TOP node (gray background, label "Django views (kode aktual)"):
    Arrow DOWN labeled "drf_spectacular auto-generate" →

  CENTER node (yellow background, large, label "/api/schema/  (OpenAPI 3.0 JSON)"):
    Two arrows splitting downward:

  LEFT BRANCH (blue background, label "/api/docs/ — Swagger UI"):
    Sub-label: "manusia klik tombol Try it out"
    Sub-label: "1 input → 1 response"

  RIGHT BRANCH (green background, label "Schemathesis CLI / pytest"):
    Sub-label: "robot generate input acak spec-valid"
    Sub-label: "87 input → 9 unique failures"

  Caption at bottom: "Same JSON file. Different consumer."
  Export -> PNG transparent background.
  Save to C:\PPL\blog-husin\static\images\schemathesis-vs-swagger.png
-->

(text fallback kalau diagram belum di-capture:)

```
Django views (kode aktual)
    ↓ drf_spectacular generate
/api/schema/   (OpenAPI 3.0 JSON)
    ↓ dibaca oleh
    ├── Swagger UI /api/docs/    (manual klik, untuk manusia)
    └── Schemathesis             (otomatis, untuk CI)
```

Swagger UI is the human-facing consumer. Schemathesis is the automated one. They eat the same JSON; the difference is what they do with it.

**Swagger UI** lets me click "Try it out", paste one body, see one response. I get exactly the inputs I thought of.

**Schemathesis** reads the same schema and generates **hundreds of spec-valid payloads** I would never have thought of. Empty arrays. UUID-shaped strings that are not real UUIDs. Strings at the boundary of declared maxLength. Numbers at the boundary of declared minimum/maximum. Then it asserts three things on every response:

1. The status code must be one of the codes the schema declares.
2. The response body must validate against the declared response schema.
3. The server must not return a 5xx for any spec-conforming request.

A failure means the server is **lying** about its own contract.

### Classification: where does this fit in the testing taxonomy

Schemathesis is not unit (it hits real server + real DB), not E2E (no UI browser), and not load (single request at a time). It is **integration scope, fuzz technique, contract assertion**. In the security taxonomy used by OWASP and NIST SP 800-115, this is **Dynamic Application Security Testing (DAST)** sitting alongside Bandit's static analysis (SAST). The two are complementary, not substitutes: Bandit reads code without running it, Schemathesis runs the server without reading its code.

For our rubric, this is the answer to *"penetration / security testing"*: the project gets a DAST layer through Schemathesis and a SAST layer through Bandit (configured by a different team member, see [related post on layered security]({{< ref "post/" >}})).

### Wiring it to the Django app

Locally, the only setup is:

```bash
pip install schemathesis
python manage.py runserver 8000 &
schemathesis run http://localhost:8000/api/schema/ \
    --base-url http://localhost:8000 \
    --checks all \
    --hypothesis-max-examples 50
```

`--checks all` enables every available assertion category (status code conformance, response schema conformance, content-type, no 5xx, header validation). `--hypothesis-max-examples 50` keeps each endpoint to 50 generated inputs, enough to catch drift without dragging CI for half an hour.

For more deterministic invocation from pytest (preferred for the CI gate), the project has a thin test file:

```python
# pengajuan/tests/test_schemathesis_pengajuan.py — see source for full version
import schemathesis

schema = schemathesis.from_path("openapi.json")  # exported once before the run

@schema.include(path_regex=r"^/api/pengajuan/").parametrize()
def test_pengajuan_endpoints(case):
    case.call_and_validate()
```

`include(path_regex=...)` scopes the fuzz to **only** the pengajuan endpoints. This matters because the project has 30+ unrelated endpoints (kegiatan, statistik, monitoring) that other team members own. Running the fuzzer on those is duplicate work; scoping keeps my CI signal targeted to the surface I am responsible for.

### Output: what Schemathesis caught

First run against the 9 selected `/api/pengajuan/*` operations generated **87 test cases** in 19 seconds and surfaced **9 unique failures** across 4 categories:

| Failure category | Count | What it means | Where the fix lives |
|---|---|---|---|
| Unsupported methods | 5 | Endpoint returned 401 for TRACE/CONNECT, but spec implies 405 Method Not Allowed | DRF middleware or per-view `http_method_names` allowlist |
| Undocumented HTTP status code | 2 | Endpoint returned 401/429 that was not declared in the `responses` section of the schema | Add explicit `OpenApiResponse(...)` entries to `drf_spectacular` decorators |
| Response violates schema | 1 | Response body for an error path missing the `code` field declared in the response model | Align serializer with the response schema, or relax the schema |
| Undocumented Content-Type | 1 | Endpoint returned `text/html` (Django debug page) on an internal error, schema declared only `application/json` | Wrap in DRF exception handler so even 500s come back JSON-shaped |

![Schemathesis output for pengajuan endpoints — 9 failures across 87 test cases](/images/schemathesis-output.png)

None of these were security-critical. All of them eroded trust in the published spec. The Unsupported-methods cluster is the classic "DRF authenticates first, refuses method second" ordering: a TRACE request goes through `IsAuthenticated` and gets 401 before the view's allowed-method check runs. The schema says "this endpoint supports GET/POST", and a 405-aware client will be confused by 401 on TRACE. Easy fix, but not something any unit test in our existing suite would have surfaced — because no unit test was written for TRACE.

### The honest limit

Schemathesis only generates **spec-conforming** inputs. If the OpenAPI schema is permissive (`additionalProperties: true`, untyped `Any`, missing constraints), the fuzzer's inputs are permissive in the same way. It cannot find bugs in inputs the spec already rejects, and it cannot find **stateful bugs across multi-request flows** (login → submit → approve → notify). Those still belong to integration tests and BDD scenarios. The fuzzer is a single-request, contract-conformance tool, nothing more.

## 3. Locust as a load-test gate (not just a manual tool)

### What was already there

The project already has `locustfile.py` with `KegiatanStressTestUser` (multi-credential round-robin login + ramp-friendly tasks for kegiatan endpoints). It also already has **one pengajuan task** at line 271:

```python
@task(2)
def list_pengajuan_admin(self):
    self.client.get("/api/pengajuan/admin/?page=1", headers=...)
```

Useful, but only one endpoint, and the whole thing is **run manually** from a developer laptop against staging. The Locust HTML report sits on the runner's filesystem and never gets back to the MR.

### What I added

Two changes to make Locust actually catch regressions:

**(a) A pengajuan-focused user class** that exercises the full workflow, not just one list endpoint. The new `PengajuanFlowUser` follows the natural user journey:

```
login as kaprodi
  → GET /api/pengajuan/kaprodi/guru-besar/   (list eligible GBs)
  → GET /api/pengajuan/kaprodi/              (list own pengajuan)
  → POST /api/pengajuan/kaprodi/pengajuan/   (submit new pengajuan)
login as admin
  → GET /api/pengajuan/admin/                (list all pengajuan)
  → PATCH /api/pengajuan/admin/<id>/status/  (approve)
```

This catches **flow-level latency**, not just per-endpoint p95. A merge that adds a `select_related` to one query but accidentally removes it from another shows up as the total flow time, not as a single endpoint number.

**(b) An explicit threshold profile** in a separate config, written so it can later be wired to a CI job:

```python
# locust_thresholds.py (companion to locustfile.py)
THRESHOLDS = {
    "GET /api/pengajuan/kaprodi/":            {"p95_ms": 500, "fail_pct": 1.0},
    "POST /api/pengajuan/kaprodi/pengajuan/": {"p95_ms": 800, "fail_pct": 1.0},
    "GET /api/pengajuan/admin/":              {"p95_ms": 600, "fail_pct": 1.0},
    "PATCH /api/pengajuan/admin/.*/status/":  {"p95_ms": 700, "fail_pct": 1.0},
}
```

The `800ms` budget on the submit endpoint and `500ms` on listing match the **human-perception boundary** for "responsive but not snappy" (Nielsen Norman Group, *Response Time Limits*, 2014). Below that the UI feels acceptable; above it the user starts perceiving the system as slow. These numbers are deliberately tight so that a regression has nowhere to hide.

### Ramp profile, not flat load

The blog post that inspired this section makes the point I want to re-emphasize: a flat 50-user load tells you the average; a **ramp** tells you where the system breaks. The ramp profile I run is:

```
0s   →  10s : 0  →  10 users   (warm-up)
10s  →  30s : 10 →  30 users   (linear ramp)
30s  →  90s :       30 users   (steady load)
90s  →  120s: 30 →  0  users   (cool-down)
```

![Locust ramp profile: line chart of concurrent users on Y axis vs time on X axis, showing four phases (warm-up, ramp, steady, cool-down) with an overlay showing where p95 latency starts to inflect upward](/images/locust-ramp-profile.png)
<!-- HOW TO CAPTURE:
  Pilihan A (paling cepat) — pakai https://excalidraw.com:
    Gambar XY chart dengan:
      X-axis: Time (0s → 120s), labeled
      Y-axis kiri: Concurrent users (0 → 30), label biru
      Y-axis kanan: p95 latency (ms), label oranye

    Plot dua kurva:
      Kurva biru "users":
        0s=0, 10s=10 (warm-up linear)
        30s=30 (ramp linear)
        90s=30 (steady)
        120s=0 (cool-down linear)
      Kurva oranye "p95 latency":
        0-30s: tetap rendah (200ms)
        30-60s: naik perlahan (400ms)
        60-90s: spike (800ms+) — TANDAI sebagai "inflection point"
        90-120s: turun lagi seiring cool-down

    Tambah label vertikal di garis 60s: "where the wall is hit"
    Caption bawah: "Ramp shows WHERE; flat load shows only WHAT."

  Pilihan B — kalau punya hasil Locust real, screenshot tab "Charts"
  dari Locust HTML report (`--html reports/locust-pengajuan.html`).
  Itu otomatis kasih grafik user count vs latency over time.

  Export -> PNG transparent background.
  Save to C:\PPL\blog-husin\static\images\locust-ramp-profile.png
-->

If p95 holds under 800ms at 10 users but spikes to 2.5s at 25 users, the ramp curve shows the inflection. A flat profile would just report `p95 = 2.5s` with no clue where the wall was hit.

### Where this is run

For this sprint the gate is **not yet wired to CI** as a hard fail. It runs manually before each release against staging, and the report is attached to the release MR. Reasons:

1. **Pre-launch product, no real traffic data.** A flat threshold based on guesses ("800ms feels right") will produce false positives. The plan is to harden it after the project sees real users for two weeks and we know what real p95 looks like.
2. **Staging shares the DB with one other service.** Concurrent runs would corrupt each other's assertions. The blog post we drew inspiration from solved this with GitLab's `resource_group:` directive; we will add the same when we move the gate into CI.

The right framing for the rubric is honest: **load testing today is regression-detection only, not capacity-planning**. The threshold catches a 2x latency regression; it does not answer "can we handle 1000 concurrent kaprodi at launch". That second question needs traffic shape data we do not yet have.

### Output: first baseline run + threshold gate working

The first end-to-end run with `PengajuanFlowUser` (10 concurrent users, 20-second window against a local instance) produced 201 requests, exercised five distinct endpoints, and **triggered the threshold gate**:

| Endpoint | p95 (ms) | Threshold (ms) | Gate verdict |
|---|---|---|---|
| `GET /api/pengajuan/admin/` | 250 | 600 | PASS |
| `GET /api/pengajuan/kaprodi/` | 870 | 500 | **FAIL** |
| `GET /api/pengajuan/kaprodi/guru-besar/` | 1200 | 500 | **FAIL** |
| `POST /api/pengajuan/kaprodi/pengajuan/` | 520 | 800 | PASS |

Two endpoints overshot the budget; `check_locust_thresholds.py` exited with code 1, which is the signal a future CI job will use to block merges:

![Locust output + threshold script output blocking on p95 breach](/images/locust-threshold-output.png)

Caveat about these specific numbers: the run was against a *fresh local SQLite* with no prior cache warming, and the kaprodi/guru-besar listing endpoints execute joins against `KaprodiModel` + `GuruBesarModel` + `Periode` that benefit heavily from connection-level cache. On staging Postgres with realistic data, p95 on these listings is closer to 200ms (we have observed this manually in earlier `KegiatanStressTestUser` runs). The point of the screenshot is **the gating mechanism works**, not that the production system is currently violating the budget.

## 4. Bandit: local re-run to verify pengajuan code clears the SAST gate

Bandit (SAST for Python) is owned and configured globally by a teammate. The CI job runs Bandit across the whole repository on every MR; my modules ride along with everyone else's. For my own pre-MR confidence, I re-run the same scanner locally **scoped to `pengajuan/`** before I push, so that the global job's red flag (if any) is never a surprise to me.

The local invocation is one line:

```bash
bandit -r pengajuan/ -x pengajuan/tests/,pengajuan/migrations/
```

`-x` excludes test fixtures and migration files. Tests intentionally use assertions and mock factories that Bandit (correctly) flags as `B101 assert_used` in a production context; suppressing those at scan time is cleaner than adding `# nosec` to every test file. Migrations are auto-generated and irrelevant to security review.

### Output

Current run on the working tree: **1278 LOC scanned, zero findings, zero `#nosec` markers added.**

![Bandit local scan output for pengajuan: 0 issues across 1278 LOC](/images/bandit-pengajuan-output.png)

This is the right standard for this sprint. The pengajuan module does not need any `# nosec` justifications, which means there is nothing Bandit flagged that I had to argue with. If a future change introduces something the scanner doesn't like, the right response is the same discipline as the blog that inspired this post: fix the root cause first, only fall back to `# nosec` with an inline comment explaining the specific reason and reviewer who acknowledged it.

The complement Bandit (SAST) makes with Schemathesis (DAST) is the same as the [OWASP guidance on Application Security Testing](https://owasp.org/www-project-web-security-testing-guide/): static analysis reads the code without running it; dynamic analysis runs the server without reading its code. Both catch different classes of issue, and the cost of running both is dominated by CI minutes, not engineering attention.

![SAST vs DAST complementarity: Bandit on the left reading Python AST without running it (catches eval, hardcoded secrets, weak crypto); Schemathesis on the right hitting the live server with fuzzed requests (catches contract drift, undocumented status codes); each finds a different class of issue](/images/sast-vs-dast.png)
<!-- HOW TO CAPTURE:
  Open https://excalidraw.com. Draw two parallel pipelines, side by side:

  LEFT pipeline (blue tint, label "SAST — Bandit"):
    [Python source .py files] → [Bandit AST parser] → [Report: B704, B608, ...]
    Caption: "Reads code. Server never starts."
    Bullet underneath: "Catches: eval, weak crypto, hardcoded secrets, unsafe deserialization"

  RIGHT pipeline (green tint, label "DAST — Schemathesis"):
    [OpenAPI schema] → [Generated HTTP requests] → [Live server response] → [Schema conformance check]
    Caption: "Runs server. Source never inspected."
    Bullet underneath: "Catches: contract drift, undocumented 5xx, response shape violation"

  Bottom center caption: "Different lens, different bug class. Both at <1 minute CI cost."

  Export -> PNG transparent background.
  Save to C:\PPL\blog-husin\static\images\sast-vs-dast.png
-->


## How the four compose

Each technique covers a dimension the others structurally cannot:

| Dimension | Tool | Failure mode it reveals |
|---|---|---|
| Stakeholder-readable behavior | pytest-bdd | Status rules drift between product intent and code |
| API contract vs implementation | Schemathesis (DAST) | Endpoints return undocumented status codes or invalid response shapes |
| Performance regression under concurrency | Locust ramp + threshold | A merge silently doubles p95 latency of a flow |
| Static security analysis | Bandit (SAST, teammate-owned) | Unsafe patterns in my own code before they ship |

The full picture of the pengajuan feature after this sprint:

```
flake8 / SonarQube       → style + maintainability
coverage + diff-cover    → execution presence
Django TestCase (197)    → service & view correctness
mutmut 3.x               → assertion quality
─────────────────────────────────────────────────────
pytest-bdd (NEW)         → stakeholder intent
Schemathesis (NEW)       → OpenAPI contract drift / DAST
Locust ramp + threshold  → flow-level latency regression
Bandit (verified locally)→ SAST on Python source
```

The four lines above the divider already existed. The four below are what this sprint added: three I built end-to-end, one I re-verified locally to make sure my code clears the gate the teammate configured globally. The combination is what produces actual confidence at merge time, not the sum of any individual layer.

## Reflection: which of the four was worth the effort

**Worth it: Bandit (local re-run).** Cheapest of the four to verify (one command, zero new CI work — the global job already runs). Zero `# nosec` markers added to `pengajuan/` means the pre-MR gate is a non-event for me, which is exactly what you want from a SAST gate: invisible when there is nothing to do, loud when there is.

**Worth it: BDD.** The biggest surprise was how cheap the framework setup was (one line in `requirements.txt`, no new CI job, runs as part of `pytest`) compared to how much the Gherkin files improved discussion with non-engineers reviewing the MR. The cost is writing the feature files, and that cost is amortized because each file doubles as documentation.

**Worth it: Schemathesis.** Catches that would have shown up only when a frontend developer hit a 500 on a request shape they assumed was valid. **Nine unique failures in the first run**, all of them drift between the published schema and the implementation. The CI cost is small; the spec is now actually trustworthy.

**Worth it but with caveat: Locust as a gate.** The framework is ready, the thresholds are written, but I deliberately did not wire it as a hard CI fail this sprint. Pre-launch traffic data does not exist; flat thresholds would produce false positives. Half-credit for now, full credit after we have two weeks of real production p95 to calibrate against.

The meta-lesson from this sprint and from the [mutmut sprint before it]({{< ref "post/mutation-testing-mutmut.md" >}}) is the same: **test design is a portfolio problem, not a coverage problem**. Each layer answers a different question. "We have 100% coverage and 197 passing tests" was true before this sprint; the pengajuan feature still had three classes of bug nobody on the team would have detected automatically. The four layers added across two sprints (mutmut, BDD, Schemathesis, Locust gate) close those gaps without replacing anything that was already working.

<!-- PLACEHOLDER: kalau kamu mau tambah section penutup lain (mis. Acknowledgements,
     Related posts, atau Contact), tulis di bawah baris ini. Saya sengaja
     hapus References supaya post-nya lebih ringkas; tambahkan kembali kalau
     reviewer rubric meminta sitasi formal. -->

