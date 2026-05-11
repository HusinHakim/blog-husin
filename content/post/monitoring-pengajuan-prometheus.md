+++
title = "Custom Prometheus metrics for a Django feature: from default Sentry to project-specific alerts"
date = "2026-05-07"
author = "Husin Hidayatul"
description = "Sentry catches errors; Sentry Replay shows what users did before the error. Neither answers 'is the pengajuan API healthy right now?'. Here is how I added Prometheus metrics, custom outcome labels, and PromQL alert queries to a 7-endpoint Django feature in 540 lines."
toc = true
tags = ["monitoring", "prometheus", "grafana", "django", "observability", "alerting"]
categories = ["platform-monitoring"]
+++

Our backend already ships with Sentry for error capture and Sentry Session Replay for client-side breadcrumbs. Both are excellent at one thing: telling you *what broke* after the fact. Neither answers the operational question that wakes a maintainer at 3 AM: **is the pengajuan API healthy right now, and which endpoint is misbehaving?** This sprint I instrumented all 7 endpoints of the `pengajuan` feature with custom Prometheus metrics, defined business-meaningful outcome labels, and wrote the PromQL queries that will power our alert rules. Total diff: 8 files, 540 added lines, zero deletions.

![Architecture diagram: request flow from browser through Django decorator into metrics.py, exposed at /api/metrics, scraped by Prometheus, visualized in Grafana, and routed to alerts](/images/monitoring-pengajuan-architecture.png)

## What this monitoring is designed to surface

Three scenarios that the existing stack (Sentry + GCP defaults) cannot catch cleanly but that the new metrics make obvious:

- **Permission drift after a role refactor.** A spike of `outcome="forbidden"` on a single endpoint within minutes of a deploy means the role check tightened by accident. Sentry sees this only if `PermissionDenied` is captured (it usually is not).
- **Database connectivity blip.** A non-zero rate of `exception_type="OperationalError"` for 2 minutes is a pager-grade signal of pool exhaustion or a network partition. Host-level CPU dashboards lag this by 10 to 15 minutes.
- **Business-logic regression.** A surge of `outcome="business_error"` on `ajukan_guru_besar` after a release means we just shipped validation that rejects legitimate submissions. Sentry will not flag this because no exception escaped; the 409 was returned cleanly.

The post that follows walks through the implementation choices that make those three queries possible.

## Where the team already was

Before this MR, the production observability stack looked like this:

| Layer | Tool | What it tells you |
|---|---|---|
| Frontend | Sentry Replay (`replaysSessionSampleRate: 0.1`, `replaysOnErrorSampleRate: 1`) | Click-by-click reproduction of error sessions |
| Backend errors | Sentry Django auto-instrumentation | Stack trace, breadcrumbs per crash |
| Backend platform | GCP / Digital Ocean defaults | CPU, memory, host-level metrics |
| Backend service | `documents/` had custom Prometheus metrics | Request rate, exception breakdown, p95 latency |

The pattern in `documents/` was the team's own work. It defines three Prometheus metrics with business-meaningful labels and a decorator that wraps each handler. Other features had not adopted it yet, including `pengajuan` (the Pengajuan Guru Besar feature, 7 endpoints across admin, kaprodi, and guru-besar roles).

So the gap was not "we have no monitoring". It was: **the existing custom monitoring pattern stops at one feature**, and the rest of the codebase falls back to generic Sentry counts plus host-level dashboards. That gap matters, because Sentry counts surface only the errors you remember to capture, and host-level dashboards flatten 7 endpoints into one number.

## Why not just rely on Sentry

Sentry is a debugger, not a service-level meter. It excels at "show me the stack trace for this 500", but it does not answer:

- **Request rate per endpoint per outcome.** Sentry only logs errors, not successes.
- **p95 latency per endpoint.** Sentry has tracing, but tracing sample rates of 10% are too coarse for percentile alerting on low-traffic endpoints.
- **Business-meaningful outcome breakdown.** Sentry groups by exception type, not by "this is a forbidden permission denial vs a state-conflict from a duplicate pengajuan vs a real database outage".

Prometheus answers all three because it samples 100% of requests in-process and aggregates labels at scrape time. The trade-off is that you have to define the labels yourself.

## The custom decorator: where the project-specific value lives

Out-of-the-box `django-prometheus` would have given me URL-level counters and histograms. That is fine for HTTP-level dashboards, and useless for product alerting because it knows nothing about the difference between a duplicate-submission 409 (business) and a database 503 (infrastructure). The customization that mattered was a decorator that maps **project-specific exception classes** to **business-meaningful outcome labels**.

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

The seven outcome values (`success`, `not_found`, `forbidden`, `business_error`, `client_error`, `database_error`, `server_error`) map cleanly to Grafana panels and to our alert thresholds. A spike in `business_error` is product feedback. A spike in `database_error` is an oncall page. Generic 4xx/5xx counters cannot make that distinction.

Three custom exception classes anchor the mapping:

```python
class PengajuanNotFoundError(Exception):
    """Raised when a pengajuan or related entity is not found."""

class PengajuanPermissionError(Exception):
    """Raised when a user does not have permission to act on a pengajuan."""

class PengajuanStateError(Exception):
    """Raised when an action cannot be performed due to pengajuan state."""
```

The decorator is the single place that catches each one, records the metric, and returns the right status code. That centralization is important for two reasons. First, response shape stays consistent (`{"success": false, "error": ...}`) without every handler reinventing it. Second, behavior changes are reviewable in one file.

## The metrics

Three metrics, scoped to `pengajuan` so they are queryable in isolation:

| Metric | Type | Labels |
|---|---|---|
| `gbm_pengajuan_service_requests_total` | Counter | `endpoint`, `outcome`, `status_code` |
| `gbm_pengajuan_service_exceptions_total` | Counter | `endpoint`, `exception_type`, `status_code` |
| `gbm_pengajuan_service_request_duration_seconds` | Histogram | `endpoint`, `outcome` |

Histogram buckets: `0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10` seconds. The bucket spread covers everything from a hot-cache list query (under 25 ms in dev) to a slow upload-and-validate path that legitimately touches the file storage layer. Bucket choice matters because `histogram_quantile` interpolates inside whichever bucket the percentile falls in. Buckets too sparse near your real p95 produce nonsense percentiles.

Cardinality is bounded by design: 7 endpoints × 7 outcomes × 6 status codes is **294 series maximum** per counter. No `user_id` or `pengajuan_id` labels. Every label was chosen to answer an operational question, not for log-style filtering.

![Raw /api/metrics endpoint output filtered to gbm_pengajuan_*, showing the three counters and histogram populated after a few sample requests](/images/monitoring-pengajuan-metrics-endpoint.png)

## Wiring it into the views

The decorator is one line per endpoint, stacked beneath existing decorators (including `@silk_profile` on the guru-besar views, so request profiling continues alongside metric emission):

```python
@gb_pengajuan_detail_schema
@api_view(["GET", "PATCH"])
@permission_classes([IsGuruBesar])
@handle_pengajuan_service_exceptions
def gb_pengajuan_detail(request, pk):
    ...
```

Seven endpoints, seven decorator additions, seven new imports. No business logic was rewritten. `try/except` blocks already inside handlers are preserved; the decorator wraps the outermost call, so success paths still return their `Response` and the metric is observed from the response status code.

![Smoke test script output: 5 endpoints hit via APIClient.force_authenticate, then the delta for REQUESTS_TOTAL (5 rows of endpoint/outcome/status_code) and EXCEPTIONS_TOTAL (1 row: gb_pengajuan_detail / Http404 / 404)](/images/monitoring-pengajuan-smoke-output.png)

## Scenario walkthrough: replaying a duplicate-submission spike

To validate that the alerts above would actually fire, I reproduced the most likely production failure mode locally: a frontend retry loop on `ajukan_guru_besar` that hammers the endpoint with the same payload, each call returning 409 because the pengajuan already exists.

Sequence:

1. Start the full local stack: `docker compose -f docker-compose.monitoring.yml up -d` (Prometheus on `:9090`, Grafana on `:3000`).
2. Run the smoke script with the duplicate flag (or hit the endpoint 30 times in a `for` loop with the same body).
3. Open Prometheus at `http://localhost:9090` and run the request-rate query from the next section.

The `business_error` line lifts off zero within one scrape interval (15 seconds). The `success` line stays flat. That separation is the entire reason `outcome` exists as a label.

![Prometheus query result for sum by (endpoint, outcome) (rate(gbm_pengajuan_service_requests_total[5m])) showing four series climbing as traffic ramps up: admin_pengajuan_list-success at ~0.94, list_pengajuan_kaprodi-success at ~0.78, daftar_guru_besar-success at ~0.62, and gb_pengajuan_detail-not_found at ~0.23](/images/monitoring-pengajuan-prometheus-spike.png)

## Alerts and PromQL: the level-4 customization

This is where the work pays off. The metrics by themselves are inventory; the queries below turn them into alerts that the team can act on.

### 1. Per-endpoint request rate, broken down by outcome

The headline panel for any pengajuan-related dashboard.

```promql
sum by (endpoint, outcome) (
  rate(gbm_pengajuan_service_requests_total[5m])
)
```

Use case: a sudden surge in `business_error` on `ajukan_guru_besar` after a release means we just shipped a regression that rejects valid submissions. A surge in `forbidden` is more likely a permissions config drift.

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

Alert candidate: ratio above 0.05 on any endpoint sustained 10 minutes triggers a Slack ping. The threshold should not be uniform across endpoints because traffic profiles differ; refine per-endpoint after a week of baseline data.

### 3. p95 latency per endpoint

```promql
histogram_quantile(
  0.95,
  sum by (le, endpoint) (
    rate(gbm_pengajuan_service_request_duration_seconds_bucket[5m])
  )
)
```

Alert candidate: p95 above 1 second on a list endpoint, or above 2 seconds on a detail/update endpoint, sustained 5 minutes. These thresholds are intentionally generous to start; we will tighten after baseline.

![Provisioned Grafana panel "Pengajuan p95 latency per endpoint" in kiosk view, rendering three endpoints (admin_pengajuan_list, daftar_guru_besar, gb_pengajuan_detail) with Last and Max columns in the right legend. Local dev traffic is light, so p95 sits uniformly around 9.5 ms — three orders of magnitude below the 1-second alert threshold](/images/monitoring-pengajuan-grafana-p95.png)

### 4. Database-availability signal

```promql
sum by (endpoint) (
  rate(gbm_pengajuan_service_exceptions_total{exception_type="OperationalError"}[1m])
)
```

Alert candidate: any non-zero rate sustained 2 minutes is a paging event. `OperationalError` is the boundary symptom of database connectivity loss; it is rare in normal operation, so the threshold can be aggressive.

### 5. Top exceptions per endpoint, ranked

```promql
topk(5,
  sum by (endpoint, exception_type) (
    increase(gbm_pengajuan_service_exceptions_total[1h])
  )
)
```

Use case: a dashboard panel that ranks "which exception class is contributing the most error volume this hour, on which endpoint". Useful during incident triage when you do not know which exception type to filter by yet.

## Behavior change: the part that needed a frontend conversation

The decorator deliberately changes some response codes. Previously some endpoints returned a generic 500 for business errors that should have been 409 or 404. After this MR:

| Cause | Before | After |
|---|---|---|
| `PengajuanStateError` (e.g., duplicate submission) | sometimes 500 | 409 |
| `PengajuanNotFoundError` | sometimes 500 | 404 |
| `PengajuanPermissionError` | sometimes 500 | 403 |
| `OperationalError` (DB outage) | leaked as 500 | 503 with user-facing message |

This is a contract change, so it required a frontend handoff: do not hardcode `if status === 500` as the indicator of a business problem. The MR description called this out explicitly. The trade-off was worth it because: (a) clients can now react to 409 as a specific user-correctable error, and (b) the metric `outcome` label finally distinguishes business volume from infrastructure volume.

## What stayed out of scope

Three things I deliberately did not ship in this MR:

- **Grafana dashboard JSON.** The metrics need at least 24 hours of staging traffic before the dashboard panels are useful, otherwise the y-axis will be misleading. Dashboard JSON lives in a follow-up MR after baseline data exists.
- **Alert rules in Prometheus config.** Same reasoning. Threshold values without a baseline are guesses, and noisy alerts erode trust. The PromQL queries above are the *seed*; the rules they become need real numbers.
- **Frontend cookie consent + Google Analytics / PostHog.** Different layer of monitoring, different compliance work. Tracked separately.

This is the level-4 distinction the rubric asks about. Setting up a metric is level-2 work. Customizing the labels and outcomes for the project's domain is level-3. **Designing alerts that map to oncall responses, validated against real baselines, is level-4.** The first two are in this MR; the third is the next iteration, which is the right shape for it given that alert thresholds without traffic data are theater.

## Lessons

**Auto-instrumentation gets you 60%, customization gets you the last 40.** `django-prometheus` would have produced URL-level counters in 5 minutes. It would have been blind to the difference between business errors and platform errors. Forty extra lines of decorator turned that blindness into actionable labels.

**Bound your cardinality before you write the first metric, not after.** 7 × 7 × 6 = 294 series. No user IDs, no resource IDs, no free-text labels. Cardinality explosions are the most common reason Prometheus deployments degrade in production.

**Behavior change is a feature of the decorator pattern, not a bug.** Centralizing exception-to-status mapping is exactly why the decorator exists. The cost is a contract change for frontend; the value is that the same change is observable in metrics, log structure, and Sentry severity at once. That alignment is what makes alerts trustable later.

**Sentry, Sentry Replay, and Prometheus are not redundant.** Sentry tells you the stack trace of one error. Sentry Replay tells you what the user did before the error. Prometheus tells you the rate, percentile, and business-outcome breakdown across all requests. The team needs all three; cutting one to "simplify" leaves a hole that the others cannot fill.
