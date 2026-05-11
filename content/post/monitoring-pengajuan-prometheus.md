+++
title = "Sentry tells me what broke. Prometheus tells me what's quietly drifting."
date = "2026-05-07"
author = "Husin Hidayatul"
description = "Bolting custom Prometheus metrics onto a 7-endpoint Django feature in 540 lines, with seven business-meaningful outcome labels and five PromQL queries ready to become alerts."
toc = true
tags = ["monitoring", "prometheus", "grafana", "django", "observability", "alerting"]
categories = ["platform-monitoring"]
+++

Sentry pages me when an endpoint crashes. It does not page me when 60% of submissions silently return 409 because we just shipped a validation regression. Different question, different tool. This sprint I bolted Prometheus instrumentation onto the seven endpoints of our `pengajuan` feature: **eight files, 540 lines added, zero deleted**. The result is a request-rate counter, an exception counter, a latency histogram, and five PromQL queries that turn each of those into an alert candidate.

![Architecture diagram: request flow from browser through Django decorator into metrics.py, exposed at /api/metrics, scraped by Prometheus, visualized in Grafana, and routed to alerts](/images/monitoring-pengajuan-architecture.png)

## Three failure modes you cannot see today

Stuff that the existing Sentry + GCP defaults stack lets slip through, but that the new metrics catch in one scrape:

- **Permission drift after a role refactor.** A spike of `outcome="forbidden"` on a single endpoint minutes after a deploy means the role check tightened by accident. Sentry only sees this if `PermissionDenied` was captured, and it usually was not.
- **Database connectivity blip.** A non-zero rate of `exception_type="OperationalError"` for 2 minutes is a pager-grade signal of pool exhaustion or a network partition. Host-level CPU graphs lag this by 10 to 15 minutes, which is roughly the same as no signal at all.
- **Silent business-logic regression.** A surge of `outcome="business_error"` on `ajukan_guru_besar` after a release means the new validation rejects legitimate submissions. Sentry does not flag it because no exception escaped; the 409 was returned cleanly. The user just goes away.

The rest of the post is the implementation that makes those three queries possible.

## Where the team already was

The production stack was not empty. It just had a feature-shaped hole in it:

| Layer | Tool | What it tells you |
|---|---|---|
| Frontend | Sentry Replay (`replaysSessionSampleRate: 0.1`, `replaysOnErrorSampleRate: 1`) | Click-by-click reproduction of error sessions |
| Backend errors | Sentry Django auto-instrumentation | Stack trace, breadcrumbs per crash |
| Backend platform | GCP / Digital Ocean defaults | CPU, memory, host-level metrics |
| Backend service | `documents/` had custom Prometheus metrics | Request rate, exception breakdown, p95 latency |

That bottom row is the interesting one. Someone on the team had already built the custom-Prometheus pattern for the `documents/` feature: three metrics with business-meaningful labels, a decorator that wraps each handler, the whole thing. It just stopped there. The other seven `pengajuan` endpoints (admin, kaprodi, guru-besar) fell back to generic Sentry counts and host-level dashboards, which is what most teams have, and what most teams quietly regret having when something starts drifting.

## Where Sentry stops being useful

Sentry is a debugger, not a service-level meter. It is excellent at "show me the stack trace for this 500", but it cannot answer the three questions I actually want to ask the system:

- **Request rate per endpoint per outcome.** Sentry counts errors, not successes. You cannot compute a ratio if you only count the numerator.
- **p95 latency per endpoint.** Sentry has tracing, but the 10% sample rate is too sparse for percentile alerting on low-traffic endpoints. You get noise, not a signal.
- **Business-meaningful outcome breakdown.** Sentry groups by exception type. I want to group by *what the user experienced*: forbidden permission, duplicate submission, database outage. Those are three completely different oncall responses.

Prometheus answers all three. It samples 100% of requests in-process, aggregates by labels at scrape time, and stays cheap as long as you keep your cardinality bounded. The catch: you have to define the labels yourself. Out of the box, you get URL paths and HTTP codes, which is exactly the wrong granularity.

## One decorator, seven outcomes

The work that mattered was 160 lines of decorator that maps **project-specific exception classes** to **business-meaningful outcome labels**. `django-prometheus` would have given me URL-level counters in five minutes. That is fine for an SRE dashboard, and useless for product alerting because it cannot tell a duplicate-submission 409 (business) from a database 503 (infrastructure). The mapping below is the entire reason this MR exists:

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

Seven outcome values (`success`, `not_found`, `forbidden`, `business_error`, `client_error`, `database_error`, `server_error`). Each one corresponds to a different action by the team. A spike in `business_error` is product feedback. A spike in `database_error` is an oncall page. Generic 4xx and 5xx counters cannot make that distinction, because they were never designed to.

Three custom exception classes anchor the mapping:

```python
class PengajuanNotFoundError(Exception):
    """Raised when a pengajuan or related entity is not found."""

class PengajuanPermissionError(Exception):
    """Raised when a user does not have permission to act on a pengajuan."""

class PengajuanStateError(Exception):
    """Raised when an action cannot be performed due to pengajuan state."""
```

One file catches all of them, records the metric, and returns the right status code. That centralization buys two things. Response shape stays consistent (`{"success": false, "error": ...}`) without every handler reinventing it. And any future behavior change is reviewable in exactly one file, not seven.

## Three metrics, 294 series, no label sprawl

Three metrics, scoped to `pengajuan` so they are queryable in isolation:

| Metric | Type | Labels |
|---|---|---|
| `gbm_pengajuan_service_requests_total` | Counter | `endpoint`, `outcome`, `status_code` |
| `gbm_pengajuan_service_exceptions_total` | Counter | `endpoint`, `exception_type`, `status_code` |
| `gbm_pengajuan_service_request_duration_seconds` | Histogram | `endpoint`, `outcome` |

Histogram buckets: `0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10` seconds. The spread covers everything from a hot-cache list query (under 25 ms in dev) to a slow upload-and-validate path that legitimately touches the file storage layer. Bucket choice matters more than people think, because `histogram_quantile` interpolates inside whichever bucket the percentile lands in. Sparse buckets near your real p95 produce nonsense percentiles, and you will not notice until the alert fires for the wrong reason.

Cardinality is bounded by design. 7 endpoints × 7 outcomes × 6 status codes is **294 series maximum** per counter. No `user_id`, no `pengajuan_id`, no free-text labels. Every label exists to answer an operational question, never to do log-style filtering. The first time someone in your team asks "can we add a `user_email` label?", say no, calmly, and then explain why.

![Raw /api/metrics endpoint output filtered to gbm_pengajuan_*, showing the three counters and histogram populated after a few sample requests](/images/monitoring-pengajuan-metrics-endpoint.png)

## One line per endpoint, seven times

The decorator is exactly one line per endpoint, stacked beneath existing decorators. On the guru-besar views, it sits below `@silk_profile`, so request profiling keeps working alongside metric emission:

```python
@gb_pengajuan_detail_schema
@api_view(["GET", "PATCH"])
@permission_classes([IsGuruBesar])
@handle_pengajuan_service_exceptions
def gb_pengajuan_detail(request, pk):
    ...
```

Seven endpoints, seven decorator additions, seven new imports. No business logic touched. Existing `try/except` blocks inside handlers were left alone, because the decorator wraps the outermost call: success paths still return their `Response`, and the metric is observed from whatever status code came out.

![Smoke test script output: 5 endpoints hit via APIClient.force_authenticate, then the delta for REQUESTS_TOTAL (5 rows of endpoint/outcome/status_code) and EXCEPTIONS_TOTAL (1 row: gb_pengajuan_detail / Http404 / 404)](/images/monitoring-pengajuan-smoke-output.png)

## Replaying a duplicate-submission spike

To prove the alerts would actually fire, I reproduced the most likely production failure mode locally. A frontend retry loop on `ajukan_guru_besar` that hammers the endpoint with the same payload, each call returning 409 because the pengajuan already exists. The kind of thing that happens at 14:00 on a Friday when nobody is watching.

Sequence:

1. Start the local stack: `docker compose -f docker-compose.monitoring.yml up -d` (Prometheus on `:9090`, Grafana on `:3000`).
2. Run the smoke script with the duplicate flag, or hit the endpoint 30 times in a `for` loop with the same body.
3. Open Prometheus at `http://localhost:9090` and run the request-rate query from the next section.

Within one scrape interval (15 seconds) the `business_error` line lifts off zero. The `success` line stays flat. That separation, on the same chart, is the entire reason `outcome` exists as a label.

![Prometheus query result for sum by (endpoint, outcome) (rate(gbm_pengajuan_service_requests_total[5m])) showing four series climbing as traffic ramps up: admin_pengajuan_list-success at ~0.94, list_pengajuan_kaprodi-success at ~0.78, daftar_guru_besar-success at ~0.62, and gb_pengajuan_detail-not_found at ~0.23](/images/monitoring-pengajuan-prometheus-spike.png)

## Five queries that become alerts

This is where the work pays off. Metrics on their own are inventory. The queries below are how that inventory becomes something a human can be paged about.

### 1. Per-endpoint request rate, broken down by outcome

The headline panel for any pengajuan-related dashboard.

```promql
sum by (endpoint, outcome) (
  rate(gbm_pengajuan_service_requests_total[5m])
)
```

Reading: a sudden surge in `business_error` on `ajukan_guru_besar` after a release means we just shipped a regression that rejects valid submissions. A surge in `forbidden` is almost always a permissions config drift. Two completely different fixes, surfaced from the same chart.

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

Alert candidate: ratio above 0.05 on any endpoint sustained for 10 minutes pings Slack. The threshold should not be uniform across endpoints; traffic profiles differ, and tuning per endpoint after a week of baseline data is the right call.

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

![Provisioned Grafana panel "Pengajuan p95 latency per endpoint" in kiosk view, rendering three endpoints (admin_pengajuan_list, daftar_guru_besar, gb_pengajuan_detail) with Last and Max columns in the right legend. Local dev traffic is light, so p95 sits uniformly around 9.5 ms, three orders of magnitude below the 1-second alert threshold](/images/monitoring-pengajuan-grafana-p95.png)

### 4. Database-availability signal

```promql
sum by (endpoint) (
  rate(gbm_pengajuan_service_exceptions_total{exception_type="OperationalError"}[1m])
)
```

Alert candidate: any non-zero rate sustained 2 minutes pages someone. `OperationalError` is the boundary symptom of database connectivity loss. It should never happen in steady state, which is precisely why the threshold can be this aggressive without becoming noise.

### 5. Top exceptions per endpoint, ranked

```promql
topk(5,
  sum by (endpoint, exception_type) (
    increase(gbm_pengajuan_service_exceptions_total[1h])
  )
)
```

Reading: this is the panel you open during an incident when you do not yet know which exception class to filter by. It ranks "which exception is generating the most error volume this hour, on which endpoint", and the answer is usually obvious within five seconds.

## Yes, this changes some response codes

The decorator deliberately changes how some failures look from the outside. A handful of endpoints used to return a generic 500 for business errors that should have been 409 or 404. After this MR:

| Cause | Before | After |
|---|---|---|
| `PengajuanStateError` (e.g., duplicate submission) | sometimes 500 | 409 |
| `PengajuanNotFoundError` | sometimes 500 | 404 |
| `PengajuanPermissionError` | sometimes 500 | 403 |
| `OperationalError` (DB outage) | leaked as 500 | 503 with user-facing message |

This is a contract change, which means it needed a frontend handoff: stop hardcoding `if status === 500` as the indicator of "something went wrong". The MR description spelled this out, and the FE side was checked before merge. The trade-off was worth it for two reasons. Clients can now react to a 409 as a specific user-correctable error ("you already submitted this, want to edit it?"), and the metric `outcome` label finally distinguishes business volume from infrastructure volume. The same change paid off in two places at once.

## Why this is level-4 work, not level-2

A monitoring rubric usually has four steps: install a tool, configure it, customize it, design alerts that drive real responses. Each step adds value, each step is also where teams typically stop and call the job done.

Installing `django-prometheus` is level-2 work. You get URL-level counters in five minutes and a dashboard nobody opens because the labels do not match how the team thinks about the product.

Customizing the labels and outcomes for the project's domain, which is what most of this post is about, is level-3 work. The dashboard now talks the team's language, but nothing is actively watching it.

**Designing alerts that map to specific oncall responses, with thresholds derived from real baseline traffic, is level-4 work.** That is the final piece, and it is the piece where this MR stops. The five PromQL queries above are seeded but not deployed as alert rules yet, because alert thresholds picked without baseline data are theater. They either fire constantly and get muted, or they never fire and create false confidence. Both outcomes are worse than no alert at all.

The one thing genuinely out of scope here is the frontend layer: cookie consent and a user-activity tracker (PostHog or Google Analytics). Different compliance work, different conversation, different MR.

## Four things I'd tell the next team

**Auto-instrumentation gets you 60% of the way. Customization gets you the last 40.** `django-prometheus` would have produced URL-level counters in five minutes and would have been blind to the difference between a business error and a platform error forever. Forty extra lines of decorator turned that blindness into labels you can actually alert on.

**Bound your cardinality before you write the first metric, not after.** 7 × 7 × 6 = 294 series. No user IDs, no resource IDs, no free-text labels. Cardinality explosions are the single most common reason Prometheus deployments degrade in production. The temptation to add "just one more label" is always there, and it is almost always a trap.

**Behavior change is the point of the decorator pattern, not a side effect.** Centralizing the exception-to-status mapping is exactly why the decorator exists. The cost is a contract change for the frontend. The value is that the same change is now visible in metrics, log structure, and Sentry severity at once. That alignment is what makes alerts trustable later, when nobody remembers why they were written.

**Sentry, Sentry Replay, and Prometheus are not redundant; they are orthogonal.** Sentry shows you the stack trace of one specific error. Sentry Replay shows you what the user did before that error. Prometheus shows you the rate, the percentile, and the business-outcome breakdown across every request the system has ever served. The team needs all three. Cutting one to "simplify the stack" leaves a hole the other two cannot fill, and the hole is exactly where the next incident will come from.
