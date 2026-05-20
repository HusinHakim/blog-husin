+++
title = "Sentry tells me what broke. Prometheus tells me what's quietly drifting."
date = "2026-05-07"
author = "Husin Hidayatul"
description = "Bolting custom Prometheus metrics onto a 7-endpoint Django feature in 540 lines, with seven business-meaningful outcome labels and five PromQL queries ready to become alerts."
toc = true
tags = ["monitoring", "prometheus", "grafana", "django", "observability", "alerting"]
categories = ["platform-monitoring"]
mermaid = true
+++

Sentry tells me when an API crashes. It does not tell me when many submissions quietly fail with a 409 because we just shipped a validation bug. The API did not crash. It just rejected the user. That is a different kind of problem, and it needs a different tool.

This sprint I added Prometheus monitoring to the seven endpoints of our `pengajuan` feature. What I got out of it: a counter for requests, a counter for exceptions, a histogram for latency, and five PromQL queries that each map to a real alert.

{{< mermaid >}}
flowchart LR
    Client["Browser /<br/>Client"]
    Maintainer((Maintainer))

    subgraph App["Application (Django backend)"]
        direction LR
        Django["View handler<br/>@handle_pengajuan_service_exceptions"]
        Metrics["metrics.py<br/>Counter + Histogram"]
        Endpoint["/api/metrics<br/>exposition"]
    end

    subgraph Stack["Observability stack"]
        direction LR
        Prom[("Prometheus<br/>time-series DB")]
        Grafana["Grafana<br/>panels + alert rules"]
    end

    Discord["Discord channel<br/>GBM_MONITORING_DISCORD"]

    Client -- "HTTP request" --> Django
    Django -- "observe()" --> Metrics
    Metrics -- "register" --> Endpoint
    Endpoint -- "scrape every 15s" --> Prom
    Prom -- "datasource" --> Grafana
    Grafana -- "dashboard" --> Maintainer
    Grafana -. "webhook when firing" .-> Discord
    Discord -. "notify" .-> Maintainer

    style App fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style Stack fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
{{< /mermaid >}}

*Request flow: browser hits a Django view wrapped by `@handle_pengajuan_service_exceptions`, the decorator calls `observe()` on the Counter and Histogram defined in `metrics.py`, those metrics are exposed at `/api/metrics` and scraped by Prometheus every 15s. Grafana reads the resulting time-series both for dashboards and for the four Grafana-managed alert rules, which fire to the team's `GBM_MONITORING_DISCORD` contact point when thresholds are breached.*

## Three problems the new metrics catch

These are three real problems that the current setup (Sentry + GCP defaults) misses. The new metrics catch all three within one scrape interval (15 seconds):

- **Permission drift after a role refactor.** If `outcome="forbidden"` suddenly spikes on one endpoint right after a deploy, the role check probably tightened by accident. Sentry only sees this if someone remembered to capture `PermissionDenied`. Usually nobody does.
- **Database connectivity blip.** If `exception_type="OperationalError"` is non-zero for 2 minutes, the database is unreachable or the pool is exhausted. This is a paging event. Host-level CPU dashboards take 10 to 15 minutes to show this, which is too late.
- **Silent business-logic regression.** If `outcome="business_error"` spikes on `ajukan_guru_besar` after a release, our new validation is rejecting submissions that used to work. Sentry will not flag it, because nothing crashed. The endpoint returned 409 normally. The user just gives up.

The rest of this post is the implementation that makes those three queries possible.

## What monitoring the team already had

The production stack was not empty. It had monitoring for one feature, and nothing for the others:

| Layer | Tool | What it tells you |
|---|---|---|
| Frontend | Sentry Replay (`replaysSessionSampleRate: 0.1`, `replaysOnErrorSampleRate: 1`) | Click-by-click reproduction of error sessions |
| Backend errors | Sentry Django auto-instrumentation | Stack trace, breadcrumbs per crash |
| Backend platform | GCP / Digital Ocean defaults | CPU, memory, host-level metrics |
| Backend service | `documents/` had custom Prometheus metrics | Request rate, exception breakdown, p95 latency |

The bottom row is the interesting one. Someone on the team had already built a custom Prometheus setup for the `documents/` feature: three metrics with project-specific labels, plus a decorator that wraps each handler. The pattern worked, but only one feature used it. The seven `pengajuan` endpoints (admin, kaprodi, guru-besar) were still using only Sentry counts and host-level dashboards. That is enough to know *something* is broken. It is not enough to know *what* or *where*.

## What Sentry cannot tell me

Sentry is a debugger, not a service-level meter. It is great at showing the stack trace for one crash. It cannot answer the three questions I actually need answered:

- **How many requests are succeeding, and how many are failing?** Sentry only counts errors. It does not count successes. Without counting successes, you cannot compute an error rate (because you only have the numerator, not the denominator).
- **How slow is each endpoint at p95?** Sentry has tracing, but the default sample rate is 10%. That is too few data points to compute reliable percentiles on low-traffic endpoints. The chart looks noisy and the alert fires on randomness.
- **What type of failure is happening?** Sentry groups by exception class. I want to group by what the *user* saw: "you do not have permission", "this is a duplicate submission", "the database is unreachable". Three different problems, three different responses.

Prometheus answers all three. It records every request in-process (no sampling), and aggregates by label at scrape time. The cost is that you have to design the labels yourself. The default `django-prometheus` library just gives you URL paths and HTTP codes, which is the wrong level of detail for product alerts.

## One decorator, seven outcomes

The most important part of the MR is a 160-line decorator. It catches our custom exception classes and tags each request with a label that describes what kind of problem happened. Without this mapping, the metrics would only know HTTP status codes, and HTTP codes cannot tell apart a duplicate submission (business problem) from a database outage (infrastructure problem). They are both "500-ish", but they need different responses.

Here is the function that turns a status code into a meaningful outcome label:

```python
def _outcome_from_status(status_code: int) -> str:
    if status_code < 400:
        return "success"
    if status_code == status.HTTP_404_NOT_FOUND:
        return "not_found"
    if status_code == status.HTTP_403_FORBIDDEN:
        return "forbidden"
    if status_code == status.HTTP_409_CONFLICT:
        return "business_error"
    if status_code < 500:
        return "client_error"
    return "server_error"
```

Seven possible values: `success`, `not_found`, `forbidden`, `business_error`, `client_error`, `database_error`, `server_error`. Each one needs a different response from the team. A spike in `business_error` is feedback for the product team. A spike in `database_error` is a page for the oncall engineer. Generic 4xx/5xx counters cannot tell these apart, so they cannot answer the question "who should look at this?".

Three custom exception classes anchor the mapping:

```python
class PengajuanNotFoundError(Exception):
    """Raised when a pengajuan or related entity is not found."""

class PengajuanPermissionError(Exception):
    """Raised when a user does not have permission to act on a pengajuan."""

class PengajuanStateError(Exception):
    """Raised when an action cannot be performed due to pengajuan state."""
```

One file (the decorator) catches all of them, records the metric, and returns the right status code. Two reasons that matters. First, every handler returns the same response shape (`{"success": false, "error": ...}`) without each one having to reimplement it. Second, when we want to change error behavior later, it lives in one file, not seven.

## Three metrics, 294 series, no label sprawl

Three metrics, scoped to `pengajuan` so they are queryable in isolation:

| Metric | Type | Labels |
|---|---|---|
| `gbm_pengajuan_service_requests_total` | Counter | `endpoint`, `outcome`, `status_code` |
| `gbm_pengajuan_service_exceptions_total` | Counter | `endpoint`, `exception_type`, `status_code` |
| `gbm_pengajuan_service_request_duration_seconds` | Histogram | `endpoint`, `outcome` |

Histogram buckets: `0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10` seconds. The range covers fast list queries (under 25 ms in dev) all the way up to slow upload-and-validate calls that touch file storage. Bucket choice matters: `histogram_quantile` estimates the percentile by interpolating *inside* whichever bucket it lands in. If the buckets near your real p95 are too far apart, the estimate is wrong, and you only notice when an alert fires for no reason.

Cardinality is also bounded on purpose. 7 endpoints × 7 outcomes × 6 status codes = **at most 294 time series** per counter. No `user_id`, no `pengajuan_id`, no free-text labels. Every label here exists to answer an operational question; none of them are for log-style filtering. If anyone asks to add a `user_email` label later, the answer is no, because that would blow up the cardinality and make Prometheus slow.

![Raw /api/metrics endpoint output filtered to gbm_pengajuan_*, showing the three counters and histogram populated after a few sample requests](/images/monitoring-pengajuan-metrics-endpoint.png)

## One line per endpoint, seven times

Adding the decorator to each endpoint is one line. On the guru-besar views it sits below the existing `@silk_profile`, so the profiling tool keeps working alongside the new metric:

```python
@gb_pengajuan_detail_schema
@api_view(["GET", "PATCH"])
@permission_classes([IsGuruBesar])
@handle_pengajuan_service_exceptions
def gb_pengajuan_detail(request, pk):
    ...
```

Seven endpoints, seven new decorator lines, seven new import lines. No business logic was changed. The existing `try/except` blocks inside each handler are still there; the decorator just wraps the whole thing. When the handler returns successfully, the decorator reads the status code off the response and records it.

![Smoke test script output: 5 endpoints hit via APIClient.force_authenticate, then the delta for REQUESTS_TOTAL (5 rows of endpoint/outcome/status_code) and EXCEPTIONS_TOTAL (1 row: gb_pengajuan_detail / Http404 / 404)](/images/monitoring-pengajuan-smoke-output.png)

## Reproducing a duplicate-submission spike locally

To check that the new alerts would actually fire on a real problem, I simulated one on my laptop. The scenario: a frontend bug that retries `ajukan_guru_besar` with the same payload over and over, each call returning 409 because the pengajuan already exists. This is a common kind of production incident.

Steps:

1. Start the local stack: `docker compose -f docker-compose.monitoring.yml up -d` (Prometheus on port 9090, Grafana on port 3000).
2. Run the smoke script with the duplicate flag, or run a simple `for` loop that hits the endpoint 30 times with the same body.
3. Open Prometheus at `http://localhost:9090` and run the first query from the next section.

After one scrape interval (15 seconds), the `business_error` line lifts off zero. The `success` line stays flat. That clean separation, both visible on the same chart, is the whole reason we have an `outcome` label.

![Prometheus query result for sum by (endpoint, outcome) (rate(gbm_pengajuan_service_requests_total[5m])) over a 15-minute window. Six series are visible: ajukan_guru_besar with two outcome labels (success near the top around 1.7 req/s, business_error around 0.5 req/s), plus admin_pengajuan_list/success, daftar_guru_besar/success, gb_pengajuan_detail/not_found, and list_pengajuan_kaprodi/success on the lower lines](/images/monitoring-pengajuan-prometheus-spike.png)

## Five queries that become alerts

Metrics on their own do not help anyone; they just sit in Prometheus. The queries below are how those metrics turn into something the team actually responds to.

### 1. Per-endpoint request rate, broken down by outcome

The headline panel for any pengajuan-related dashboard.

```promql
sum by (endpoint, outcome) (
  rate(gbm_pengajuan_service_requests_total[5m])
)
```

How to read it: a sudden spike of `business_error` on `ajukan_guru_besar` after a release usually means we shipped a bug that rejects valid submissions. A spike of `forbidden` usually means a permissions config drift. Two different problems, two different fixes, both visible from the same chart.

### 2. Error ratio per endpoint

```promql
sum by (endpoint) (
  rate(gbm_pengajuan_service_exceptions_total{outcome!="success"}[5m])
)
/
sum by (endpoint) (
  rate(gbm_pengajuan_service_requests_total[5m])
)
```

Suggested alert: if the ratio is above 0.05 on any endpoint for 10 minutes straight, send a Slack ping. The threshold should not be the same on every endpoint, because some endpoints get more traffic than others. The right move is to tune the threshold per endpoint after about a week of real traffic.

### 3. p95 latency per endpoint

```promql
histogram_quantile(
  0.95,
  sum by (le, endpoint) (
    rate(gbm_pengajuan_service_request_duration_seconds_bucket[5m])
  )
)
```

Suggested alert: p95 above 1 second on a list endpoint, or above 2 seconds on a detail or update endpoint, for 5 minutes straight. These thresholds are deliberately loose at the start. We will tighten them once we have a week of real data to compare against.

![Provisioned Grafana dashboard "Pengajuan Monitoring" showing two panels. Top panel renders the request rate per endpoint and outcome (ajukan_guru_besar/success around 1.7 req/s as the dominant line). Bottom panel renders Pengajuan p95 latency per endpoint, with list_pengajuan_kaprodi visibly slower at around 450 ms while the other endpoints sit between 25 and 235 ms. The synthetic data is generated by a weighted local emitter, but the visualization is exactly what production would show](/images/monitoring-pengajuan-grafana-p95.png)

### 4. Database-availability signal

```promql
sum by (endpoint) (
  rate(gbm_pengajuan_service_exceptions_total{exception_type="OperationalError"}[1m])
)
```

Suggested alert: if this rate is non-zero for 2 minutes straight, page someone. `OperationalError` is what Django raises when it cannot reach the database. It should never happen in normal operation. Because it should be zero, the threshold can be very aggressive without producing noisy alerts.

### 5. Top exceptions per endpoint, ranked

```promql
topk(5,
  sum by (endpoint, exception_type) (
    increase(gbm_pengajuan_service_exceptions_total[1h])
  )
)
```

How to read it: this is the panel I open during an incident when I do not yet know which exception class is causing the trouble. It ranks "which exception class is producing the most errors right now, and on which endpoint". The answer is usually obvious in a few seconds.

## What it looks like when one of these alerts fires

The five queries above are not theoretical. After confirming with the team that our alerting is **Grafana-managed** (rules, contact points, and notification policies all defined as Grafana provisioning YAML inside the `grafana-alerting` ConfigMap, not in Prometheus Alertmanager), I wrote the four pengajuan rules in Grafana provisioning format under a new `pengajuan-alerts.yaml` data key in `k8s/config/grafana-alerting.yaml`. Rule names: `PengajuanDatabaseError`, `PengajuanHighErrorRatio`, `PengajuanSlowListEndpoint`, `PengajuanBusinessErrorSpike`. All four route to the existing `GBM_MONITORING_DISCORD` contact point, which resolves the Discord webhook URL from the `DISCORD_WEBHOOK_URL` env var injected at deploy time.

The setup, for reproducibility:

1. Prometheus scrapes the `/api/metrics` endpoint as usual.
2. Grafana evaluates a Grafana-managed alert rule on `gbm_pengajuan_service_requests_total{outcome="business_error"}` with threshold `> 0.1 req/s` and `for: 1m` (lower than the production threshold of `0.5 req/s` to make it trigger quickly in a dev environment).
3. Grafana contact point: a Discord webhook pointing to a test channel I created.
4. Trigger: a hot-loop traffic generator hitting the `ajukan_guru_besar` endpoint to push the business_error rate well above the threshold.

Within about 90 seconds the rule transitions `Normal → Pending → Firing`, Grafana POSTs to Discord, and the alert lands in the channel:

![Discord screenshot showing two messages. The first at 21:36 from "Grafana APP" with the full Grafana alert template: red circle, [FIRING] PengajuanBusinessErrorSpike (demo), Endpoint: submit_pengajuan, Severity: warning, Summary, Description, Started: 2026-05-11 14:36:10 UTC, Current value: map[A:48.72904469253427 C:1], with Grafana v11.3.0 footer. The second message at 21:54 from "Grafana Test (manual proof)" is the same content posted directly via a webhook curl to confirm the channel works independently of Grafana](/images/monitoring-pengajuan-alert-discord.png)

The first message (21:36) is the real Grafana-fired alert. Note the `Current value: map[A:48.72904469253427 C:1]` line at the bottom: that is the actual rate the Grafana evaluator saw at fire time. The endpoint label `submit_pengajuan` comes from the metric instrumentation I added in `pengajuan/decorators.py`. End to end, every label in that message originated from code in this MR.

The second message (21:54) is a separate manual sanity check: a curl directly to the Discord webhook with a hand-crafted payload, to confirm that the webhook channel itself is healthy and not the bottleneck. Both messages arriving in the same channel confirms two things at once: the wire works, and the channel works.

A production deploy is the next step. Once the rules from `k8s/config/prometheus-alerts.yaml` are picked up by the team's production Prometheus and the team's Grafana is pointed at them, the team's existing `#grafana-alerts` channel will start receiving these pengajuan-specific alerts on top of the generic project-wide 5xx counter that already runs there.

## Extension: tracking state transitions as business events

After review, a teammate pointed out that the HTTP-level metrics above count requests but do not count *what those requests do to the business state*. A 200 response on `admin_pengajuan_update_status` could mean an approval, a rejection, or a status revert. The HTTP layer cannot tell them apart, and a PM-facing dashboard needs that distinction.

The follow-up is one more Counter at the service layer:

```python
PENGAJUAN_STATE_TRANSITIONS_TOTAL = Counter(
    "gbm_pengajuan_state_transitions_total",
    "Business state transitions of pengajuan grouped by from_status and to_status.",
    ["from_status", "to_status"],
)
```

Three transitions get instrumented:
- Creation: `from_status="none"`, `to_status="menunggu"` (kaprodi submits)
- Approval: `from_status="menunggu"`, `to_status="disetujui"` (admin approves)
- Rejection: `from_status="menunggu"`, `to_status="ditolak"` (admin rejects)

### The detail that matters: `transaction.on_commit`

If you call the counter inside `transaction.atomic()` and the transaction rolls back, the counter would still hold the increment. That is a phantom count: the database never changed, but the metric thinks it did. Over time this corrupts your dashboards in a way that is hard to detect.

The fix is `transaction.on_commit()`, which queues a callback to run *after* the transaction commits successfully:

```python
with transaction.atomic():
    old_status = pengajuan.status
    pengajuan.status = new_status
    pengajuan.save()
    transaction.on_commit(
        lambda: record_pengajuan_state_transition(old_status, new_status)
    )
```

If the `with` block exits via exception (rollback), the callback never fires. The counter stays accurate.

A dedicated test enforces this property: open a `transaction.atomic()` block, force `transaction.set_rollback(True)`, exit the block, then assert the counter is unchanged. Without `on_commit`, that test would fail.

### Two layers, two audiences

The HTTP-level metrics (request rate, p95 latency, exception counts) are good for SREs answering "is the service healthy right now". The business-level metric (state transitions) is good for PMs answering "how many pengajuan get approved per week, what is the rejection ratio". Same Prometheus, same Grafana, but the two metrics serve different questions and live at different layers of the code (decorator vs service function).

## This changes some response codes (intentionally)

The decorator deliberately changes how some failures look from the outside. Before this MR, a few endpoints returned a generic 500 for business problems that should have been 409 or 404. After this MR:

| Cause | Before | After |
|---|---|---|
| `PengajuanStateError` (e.g., duplicate submission) | sometimes 500 | 409 |
| `PengajuanNotFoundError` | sometimes 500 | 404 |
| `PengajuanPermissionError` | sometimes 500 | 403 |
| `OperationalError` (DB outage) | leaked as 500 | 503 with user-facing message |

This is an API contract change, so the frontend team needed a heads-up: stop assuming `status === 500` means "something went wrong". The MR description spelled out the new status codes, and the FE side was checked before merge. Two reasons this was worth doing. First, the FE can now respond to a 409 with a helpful, user-correctable message (for example: "you already submitted this, want to edit it?"). Second, the `outcome` label on the metric finally separates business problems from infrastructure problems. One code change improved two things at once.

## End-to-end verification through the real frontend

To verify the wire survives a real user flow, I drove the Next.js frontend with Playwright as three roles: kaprodi, admin, and (via the underlying HTTP) guru besar. Every action below was triggered by an actual button click in the browser, going through real JWT authentication, real HTTP, and real metric increments. The smoke script in `scripts/smoke_pengajuan_metrics.py` bypassed FE and JWT layers with `APIClient.force_authenticate`; this run does not.

### Step 1: Kaprodi browses the guru besar list

![Kaprodi role opens the Daftar Guru Besar page showing three GB candidates with Ajukan Guru Besar buttons per row](/images/manual-flow-01-kaprodi-daftar-gb.png)

Loading this page goes through the `daftar_guru_besar` endpoint. Counter `gbm_pengajuan_service_requests_total{endpoint="daftar_guru_besar", outcome="success", status_code="200"}` increments by 1.

### Step 2: Kaprodi sees their submitted pengajuan

![Kaprodi Pengajuan Saya page showing three rows with badge SEDANG DIPROSES, each pointing to a different Prof Test GB target](/images/manual-flow-02-kaprodi-pengajuan-saya-list.png)

After clicking Ajukan on three GB candidates, the kaprodi opens "Pengajuan Saya" and sees all three rows with `SEDANG DIPROSES`. Each submission previously hit `ajukan_guru_besar / success / 201`, and each one increments `gbm_pengajuan_state_transitions_total{from_status="none", to_status="menunggu"}` via the `transaction.on_commit` callback. The list page itself increments `list_pengajuan_kaprodi / success / 200`.

### Step 3: Admin sees all incoming pengajuan

![Admin Manajemen Pengajuan page listing the same three pengajuan, all with SEDANG DIPROSES badges and Terima/Tolak action buttons](/images/manual-flow-05-admin-list.png)

Admin opens the Manajemen Pengajuan dashboard. The page load goes through `admin_pengajuan_list / success / 200`. The buttons next to each row are what will trigger the state transitions in the next two steps.

### Step 4: Admin approves the first pengajuan

![After clicking Terima on the first row, the badge changes from SEDANG DIPROSES to DITERIMA in green, while the other two rows remain unchanged](/images/manual-flow-06-admin-after-approve.png)

Clicking the green "Terima" button on the first row changes its status badge from `SEDANG DIPROSES` to `DITERIMA`. Endpoint `admin_pengajuan_update_status / success / 200` increments. State transition counter `gbm_pengajuan_state_transitions_total{from_status="menunggu", to_status="disetujui"}` increments by 1, fired by the `transaction.on_commit` callback in `pengajuan/services.py`. An existing notification system (untouched by this MR) emails the kaprodi.

### Step 5: Admin rejects the second pengajuan

![After clicking Tolak with a reason on the second row, its badge becomes DITOLAK in red with the rejection reason displayed inline; the third row stays SEDANG DIPROSES](/images/manual-flow-07-admin-after-reject.png)

Clicking "Tolak" opens a reason input modal; after submitting, the second row shows `DITOLAK` with the reason "Tidak memenuhi syarat untuk demo." printed underneath. State transition counter `gbm_pengajuan_state_transitions_total{from_status="menunggu", to_status="ditolak"}` increments by 1. The third row is left untouched as a control.

### Underlying HTTP, all in one composite

![Composite log of all six HTTP request/response cycles captured against the local Django backend: kaprodi submit (201), duplicate submit (409), guru besar list and detail (200), admin approve (200 disetujui), admin reject (200 ditolak)](/images/manual-flow-api-walkthrough.png)

The screenshots above show only what the user sees. For completeness, this composite captures the actual HTTP request/response cycle for the same logical steps, including the duplicate-submission `409 Conflict` case that the UI surfaces as a toast but is most clearly visible at the wire level. Status codes are colour-coded: green for 2xx, yellow for 4xx.

### Step 6: Alert fires through Grafana to Discord

In an earlier session I left a script hammering the duplicate-submission endpoint at a rate that pushes `business_error` above 0.1 req/s for one full minute (the local-dev threshold; production uses 0.5 req/s for five minutes). Grafana evaluates the rule every 30 seconds; the state transitions Normal → Pending → Firing inside 90 seconds. The configured Discord contact point receives the message a few seconds later.

![Discord channel showing the [FIRING] PengajuanBusinessErrorSpike alert message posted by Grafana, with endpoint ajukan_guru_besar, severity warning, current rate 48.73 req/s well above the 0.1 threshold, and the rule source pointing to k8s/config/prometheus-alerts.yaml at commit 9f9dc70](/images/monitoring-pengajuan-alert-discord.png)

The screenshot above is from the personal test Discord channel I configured for this demo, not the team's `#grafana-alerts` channel. Once this MR merges and the updated `grafana-alerting` ConfigMap is applied to the cluster, production Grafana will pick up the same four rules and route them to the team channel using the existing `GBM_MONITORING_DISCORD` contact point.

## Four lessons

**Default instrumentation gets you most of the way. The customization gets you the rest.** Using `django-prometheus` out of the box would have produced URL-level counters in five minutes, and would never have been able to tell business errors apart from platform errors. The extra decorator code is what turns "we have metrics" into "we have metrics we can alert on".

**Decide your label set before you write the first metric.** 7 × 7 × 6 = 294 time series. No user IDs, no resource IDs, no free-text. High-cardinality labels are the most common reason Prometheus gets slow in production. The wish to add "just one more useful label" is always there, and almost always a mistake.

**A response-code change is a feature of the decorator, not a problem.** Centralizing the mapping from exceptions to status codes is exactly why the decorator exists. The cost is one heads-up to the frontend team. The benefit is that the same fix improves the metrics, the logs, and the Sentry severity, all at once. That kind of alignment is what makes alerts trustworthy six months later when nobody remembers why they were written.

**Sentry, Sentry Replay, and Prometheus are not the same kind of tool.** Sentry shows you the stack trace of one error. Sentry Replay shows you what the user clicked before that error happened. Prometheus shows you the request rate, the latency percentile, and the outcome breakdown across every request the system has ever served. All three answer different questions. The team needs all three; dropping one to "simplify the stack" creates a blind spot that the other two cannot cover.
