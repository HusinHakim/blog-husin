+++
title = "Sentry tells me what broke. Prometheus tells me what's quietly drifting."
date = "2026-05-07"
author = "Husin Hidayatul"
description = "Bolting custom Prometheus metrics onto a 7-endpoint Django feature in 540 lines, with seven business-meaningful outcome labels and five PromQL queries ready to become alerts."
toc = true
tags = ["monitoring", "prometheus", "grafana", "django", "observability", "alerting"]
categories = ["platform-monitoring"]
+++

Sentry tells me when an API crashes. It does not tell me when many submissions quietly fail with a 409 because we just shipped a validation bug. The API did not crash. It just rejected the user. That is a different kind of problem, and it needs a different tool.

This sprint I added Prometheus monitoring to the seven endpoints of our `pengajuan` feature. **Eight files, 540 lines added, zero deleted.** What I got out of it: a counter for requests, a counter for exceptions, a histogram for latency, and five PromQL queries that each map to a real alert.

![Architecture diagram: request flow from browser through Django decorator into metrics.py, exposed at /api/metrics, scraped by Prometheus, visualized in Grafana, and routed to alerts](/images/monitoring-pengajuan-architecture.png)

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

![Prometheus query result for sum by (endpoint, outcome) (rate(gbm_pengajuan_service_requests_total[5m])) showing four series climbing as traffic ramps up: admin_pengajuan_list-success at ~0.94, list_pengajuan_kaprodi-success at ~0.78, daftar_guru_besar-success at ~0.62, and gb_pengajuan_detail-not_found at ~0.23](/images/monitoring-pengajuan-prometheus-spike.png)

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

![Provisioned Grafana panel "Pengajuan p95 latency per endpoint" in kiosk view, rendering three endpoints (admin_pengajuan_list, daftar_guru_besar, gb_pengajuan_detail) with Last and Max columns in the right legend. Local dev traffic is light, so p95 sits uniformly around 9.5 ms, three orders of magnitude below the 1-second alert threshold](/images/monitoring-pengajuan-grafana-p95.png)

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

## This changes some response codes (intentionally)

The decorator deliberately changes how some failures look from the outside. Before this MR, a few endpoints returned a generic 500 for business problems that should have been 409 or 404. After this MR:

| Cause | Before | After |
|---|---|---|
| `PengajuanStateError` (e.g., duplicate submission) | sometimes 500 | 409 |
| `PengajuanNotFoundError` | sometimes 500 | 404 |
| `PengajuanPermissionError` | sometimes 500 | 403 |
| `OperationalError` (DB outage) | leaked as 500 | 503 with user-facing message |

This is an API contract change, so the frontend team needed a heads-up: stop assuming `status === 500` means "something went wrong". The MR description spelled out the new status codes, and the FE side was checked before merge. Two reasons this was worth doing. First, the FE can now respond to a 409 with a helpful, user-correctable message (for example: "you already submitted this, want to edit it?"). Second, the `outcome` label on the metric finally separates business problems from infrastructure problems. One code change improved two things at once.

## Four lessons

**Default instrumentation gets you most of the way. The customization gets you the rest.** Using `django-prometheus` out of the box would have produced URL-level counters in five minutes, and would never have been able to tell business errors apart from platform errors. The extra decorator code is what turns "we have metrics" into "we have metrics we can alert on".

**Decide your label set before you write the first metric.** 7 × 7 × 6 = 294 time series. No user IDs, no resource IDs, no free-text. High-cardinality labels are the most common reason Prometheus gets slow in production. The wish to add "just one more useful label" is always there, and almost always a mistake.

**A response-code change is a feature of the decorator, not a problem.** Centralizing the mapping from exceptions to status codes is exactly why the decorator exists. The cost is one heads-up to the frontend team. The benefit is that the same fix improves the metrics, the logs, and the Sentry severity, all at once. That kind of alignment is what makes alerts trustworthy six months later when nobody remembers why they were written.

**Sentry, Sentry Replay, and Prometheus are not the same kind of tool.** Sentry shows you the stack trace of one error. Sentry Replay shows you what the user clicked before that error happened. Prometheus shows you the request rate, the latency percentile, and the outcome breakdown across every request the system has ever served. All three answer different questions. The team needs all three; dropping one to "simplify the stack" creates a blind spot that the other two cannot cover.
