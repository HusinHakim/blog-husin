+++
title = "Beyond unit tests for a Django feature: BDD, schema fuzzing, load gating, and SAST"
date = "2026-05-11T05:00:00+07:00"
author = "Husin Hidayatul"
description = "Adding BDD, Schemathesis, a Locust gating threshold, and a Bandit local re-run on top of an already mature unit-test suite for the Pengajuan feature."
toc = true
tags = ["bdd", "schemathesis", "locust", "load-testing", "bandit", "sast", "django", "testing", "qa"]
categories = ["software-testing"]
+++

A unit test that asserts `service.update_status(...) == "disetujui"` does not tell you the **stakeholder-readable rule** behind that transition. It does not catch the spec-valid payloads the engineer never thought to write, nor a merge that silently doubles p95 latency.

This post walks through four layers I added on top of an existing unit-test suite for the `pengajuan` feature: pytest-bdd, Schemathesis, a Locust gating profile, and a local Bandit scan. Our project lives on internal GitLab; *MR* throughout this post means **Merge Request**.

![Testing stack overview: existing layers on the left (unit, integration, coverage, mutation, lint); four new layers on the right (BDD, Schemathesis, Locust gate, Bandit local re-run), each pointing to the bug class it catches](/images/testing-stack-overview.png)

Before this sprint the `pengajuan` feature already had flake8 + SonarQube for style, `coverage` with diff-cover at 100% on MR, 197 unit and integration tests across the Django `TestCase` files, and mutmut 3.x scoped to services and permissions (see [previous post]({{< ref "post/mutation-testing-mutmut.md" >}})).

What was missing: an executable spec readable by non-engineers, drift detection between the OpenAPI schema and the running server, and a load signal that actually gates merges instead of sitting in a developer's terminal.

## 1. BDD with pytest-bdd: executable spec for status transitions

The naive argument against BDD is that it duplicates unit-test scope in more verbose syntax. That is true when the engineer writes both the spec and the Python. Pengajuan's workflow rules (one submission per gurubesar per periode, `catatan_penolakan` mandatory when status is `ditolak`, `rekomendasi_gb` only after `disetujui`) come from product, not engineering. BDD makes the **rule** the document of record.

I picked `pytest-bdd` over `behave` because the rest of the suite is already pytest-compatible via `pytest-django`. Same fixtures, same conftest, same coverage report.

![BDD execution flow: Gherkin feature file on the left, Python step file binding Given/When/Then in the middle, pytest runner producing PASSED/FAILED on the right](/images/bdd-execution-flow.png)

Excerpt from `pengajuan/tests/features/pengajuan_workflow.feature`:

```gherkin
Scenario: Admin menolak pengajuan tanpa catatan ditolak validasi
  Given ada pengajuan dengan status "menunggu"
  When admin mencoba menolak pengajuan tanpa catatan_penolakan
  Then permintaan ditolak dengan error "catatan_penolakan wajib diisi"
  And status pengajuan tetap "menunggu"
```

A product owner can answer "what happens when admin rejects without a reason?" by reading the file. The suite now contains **5 scenarios in 1 feature file, all passing in 6.28 seconds** as part of the regular pytest run.

CI integration was two lines: install `pytest-bdd pytest-django`, add a `bdd:` job alongside the existing `test:` job.

![pytest-bdd output for pengajuan scenarios: 5 passed in 6.28s](/images/bdd-pytest-output.png)

The honest limit: pytest-bdd is most valuable for behaviors **negotiated with stakeholders**. I scoped it only to status transitions, duplicate constraints, and `rekomendasi_gb`. CRUD plumbing stays in regular Django `TestCase`, where it belongs.

## 2. Schemathesis: fuzzing the OpenAPI contract

`drf_spectacular` generates an OpenAPI schema at `/api/schema/`. Swagger UI lets a human click "Try it out" with one body at a time. Schemathesis reads the same schema and generates **hundreds of spec-valid payloads** the engineer would never write, then asserts that the response status, body, and content-type all conform to what the spec declared.

A failure means the server is **lying** about its own contract.

![OpenAPI schema as single source feeding two consumers: drf_spectacular generates schema, then Swagger UI (human, manual click) and Schemathesis (robot, CI fuzz) both read it](/images/schemathesis-vs-swagger.png)

The pytest-driven invocation scopes the fuzz to only the pengajuan surface I own:

```python
# pengajuan/tests/test_schemathesis_pengajuan.py
import schemathesis

schema = schemathesis.from_path("openapi.json")

@schema.include(path_regex=r"^/api/pengajuan/").parametrize()
def test_pengajuan_endpoints(case):
    case.call_and_validate()
```

The first run against 9 `/api/pengajuan/*` operations generated **87 test cases in 19 seconds and surfaced 9 unique failures**:

| Failure category | Count | What it means |
|---|---|---|
| Unsupported methods | 5 | 401 returned for TRACE/CONNECT where spec implies 405 |
| Undocumented status code | 2 | 401/429 returned but never declared in `responses` |
| Response violates schema | 1 | Error body missing the `code` field |
| Undocumented Content-Type | 1 | Django debug HTML leaking on a 500 path |

![Schemathesis output for pengajuan endpoints: 9 failures across 87 test cases](/images/schemathesis-output.png)

None were security-critical; all eroded trust in the published spec.

The TRACE/CONNECT cluster is the classic "DRF authenticates before checking method" ordering, which no unit test in the existing suite would surface because nobody writes unit tests for TRACE.

The honest limit: Schemathesis only generates **spec-conforming** inputs. If the schema is permissive (`additionalProperties: true`, untyped `Any`), the fuzzer is permissive in the same way.

It also cannot find **stateful bugs across multi-request flows** (login → submit → approve). Those still belong to BDD and integration tests.

## 3. Locust as a load-test gate (not just a manual tool)

The project already had `locustfile.py` with `KegiatanStressTestUser` and exactly one pengajuan task, run manually from a laptop against staging. Useful, but the result never made it back to the MR. I added two things.

**A pengajuan-focused user class** (`PengajuanFlowUser`) that exercises the natural journey: kaprodi lists eligible GBs, lists own pengajuan, submits a new one; admin lists all pengajuan and approves. This catches **flow-level latency**, not just per-endpoint p95. A merge that adds a `select_related` to one query but accidentally removes it from another surfaces as the total flow time.

**An explicit threshold profile** in `locust_thresholds.py` with `p95_ms` and `fail_pct` budgets per endpoint. The 800 ms budget on submit and 500 ms on listings match the human-perception boundary for "responsive but not snappy" (Nielsen Norman Group, *Response Time Limits*, 2014).

A flat 50-user load tells you the average; a **ramp** tells you where the system breaks. The profile I run is warm-up (0 → 10 users in 10 s), linear ramp (10 → 30 users in 20 s), steady 30 users for a minute, cool-down to zero.

![Locust ramp profile: concurrent users on Y axis vs time on X axis, four phases (warm-up, ramp, steady, cool-down) with an overlay showing where p95 inflects upward](/images/locust-ramp-profile.png)

The gate is not yet wired to CI as a hard fail: pre-launch traffic data does not exist, so flat thresholds would produce false positives, and staging shares a DB with one other service. For now it runs before each release and the report is attached to the release MR.

### First baseline run

10 concurrent users, 20-second window, local instance, 201 requests across five endpoints. Two listings overshot the budget; `check_locust_thresholds.py` exited 1, which is the signal a future CI job will use.

| Endpoint | p95 (ms) | Threshold (ms) | Gate |
|---|---|---|---|
| `GET /api/pengajuan/admin/` | 250 | 600 | PASS |
| `GET /api/pengajuan/kaprodi/` | 870 | 500 | **FAIL** |
| `GET /api/pengajuan/kaprodi/guru-besar/` | 1200 | 500 | **FAIL** |
| `POST /api/pengajuan/kaprodi/pengajuan/` | 520 | 800 | PASS |

![Locust output and threshold script blocking on p95 breach](/images/locust-threshold-output.png)

Caveat about the specific numbers: the run was against fresh local SQLite with no cache warming, and the failing listings execute joins against `KaprodiModel + GuruBesarModel + Periode` that benefit heavily from connection-level cache. On staging Postgres we have observed p95 closer to 200 ms.

The point of the screenshot is **the gating mechanism works**, not that production currently violates the budget. Load testing today is regression-detection, not capacity-planning.

## 4. Bandit: local re-run to verify pengajuan clears the SAST gate

Bandit is owned globally by a teammate; the CI job runs it across the whole repository on every MR. Schemathesis (DAST) reads no source; Bandit (SAST) reads the source without running it. The two are complementary, as the OWASP testing guide describes.

For pre-MR confidence I re-run the same scanner locally, scoped to `pengajuan/`:

```bash
bandit -r pengajuan/ -x pengajuan/tests/,pengajuan/migrations/
```

`-x` excludes test fixtures (Bandit correctly flags `B101 assert_used` in tests) and auto-generated migrations.

![SAST vs DAST: Bandit reads Python AST without running it; Schemathesis hits the live server with fuzzed requests; each finds a different class of issue](/images/sast-vs-dast.png)

Current run on the working tree: **1278 LOC scanned, zero findings, zero `#nosec` markers added.**

![Bandit local scan output for pengajuan: 0 issues across 1278 LOC](/images/bandit-pengajuan-output.png)

That is the right standard for this sprint. If a future change introduces something the scanner doesn't like, the right response is to fix the root cause, not to paper over with `# nosec`.

## How the four compose

| Dimension | Tool | Failure mode it reveals |
|---|---|---|
| Stakeholder-readable behavior | pytest-bdd | Status rules drift from product intent |
| API contract vs implementation | Schemathesis (DAST) | Undocumented status codes, invalid response shapes |
| Performance regression | Locust ramp + threshold | A merge silently doubles p95 of a flow |
| Static security | Bandit (SAST) | Unsafe patterns before they ship |

## Reflection

- **Bandit (local re-run):** worth it. One command, no new CI work, zero `# nosec` markers on `pengajuan/`. Invisible when there is nothing to do.
- **BDD:** worth it. One line in `requirements.txt`, no new runner, and the Gherkin doubles as documentation for non-engineers reviewing the MR.
- **Schemathesis:** worth it. Nine drift failures in the first run, all of them invisible to the existing 197 unit tests.
- **Locust gate:** worth it with caveat. Framework ready, thresholds written, but I did not wire it as a hard fail this sprint. Half-credit now, full credit once we have two weeks of real p95 to calibrate against.

The meta-lesson from this sprint and the [mutmut sprint before it]({{< ref "post/mutation-testing-mutmut.md" >}}) is the same: **test design is a portfolio problem, not a coverage problem**. Each layer answers a different question.

"We have 100% coverage and 197 passing tests" was true before this sprint, and the pengajuan feature still had three classes of bug the team would not have detected automatically. The four layers added across two sprints close those gaps without replacing anything that was already working.
