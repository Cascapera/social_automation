# Alerting Runbook

This runbook covers the bootstrap Prometheus alerts defined in `monitoring/alert_rules.yml`.

These thresholds are conservative starting points, not guaranteed production truth. Tune them after observing normal traffic patterns.

## Dashboards to open first

- `Service SLOs`: current SLO-oriented health view
- `Queue Operations`: queue wait, backlog, throughput and failures
- `Stage Durations`: transcription, render and publish latency
- `Publish Operations`: publish-specific attempts, failures and quota behavior

On `Service SLOs`, the two compact summary panels for queue wait/backlog are normalized against the actual alert thresholds:

- value `< 1.0`: below alert threshold
- value `>= 1.0`: at or above alert threshold
- value `>= 2.0`: materially above the bootstrap alert threshold

## Alerts

### `QueueBacklogHigh`

Meaning:
- A ready queue stayed above its bootstrap backlog threshold for 15 minutes.

First checks:
- Open `Service SLOs` and compare `Queue backlog by queue` with `Queue wait p95 by queue`.
- If backlog and wait rise together, the queue is likely saturated.
- If backlog rises but wait stays flat, check whether the queue is draining normally and whether the spike is just a short burst.

### `QueueWaitHigh`

Meaning:
- Queue wait p95 stayed above the bootstrap threshold for 15 minutes.

First checks:
- Compare queue wait and backlog on `Service SLOs`.
- Check `Queue Operations` for throughput and failures on the same queue.
- For `publish`, remember some flows intentionally use Celery `countdown`/staggering; the alert threshold is deliberately very conservative to reduce noise.

### `TranscriptionLatencyHigh`

Meaning:
- Transcription p95 runtime stayed above the bootstrap threshold.

First checks:
- Open `Stage Durations`.
- Check `Queue Operations` for transcription queue wait and backlog.
- Verify worker health, model/runtime issues and host CPU pressure.

### `RenderLatencyHigh`

Meaning:
- Render p95 runtime stayed above the bootstrap threshold.

First checks:
- Open `Stage Durations`.
- Check render backlog and wait first.
- Verify GPU availability, FFmpeg failures and host-level resource contention.

### `PublishLatencyHigh`

Meaning:
- Publish p95 runtime stayed above the bootstrap threshold.

First checks:
- Open `Stage Durations` and `Publish Operations`.
- Check whether failures are also rising; if yes, suspect upstream platform/API instability.
- If latency rises without failures, inspect publish queue pressure and outbound bandwidth.

### `TranscriptionFailuresHigh`

Meaning:
- Transcription failure count and failure rate both crossed the bootstrap threshold.

First checks:
- Open `Service SLOs` and `Stage Durations`.
- Check recent worker/runtime errors and model initialization failures.
- Confirm this is not just one bad asset causing repeated failures.

### `RenderFailuresHigh`

Meaning:
- Render failure count and failure rate both crossed the bootstrap threshold.

First checks:
- Inspect FFmpeg/GPU logs first.
- Compare render failures with render backlog and wait.
- Confirm whether failures are broad or tied to one problematic media/input pattern.

### `PublishFailuresHigh`

Meaning:
- Publish failure count and failure rate both crossed the bootstrap threshold.

First checks:
- Open `Publish Operations` and `Service SLOs`.
- If failures rise without queue pressure, suspect external platform/API instability, auth problems or quota issues.
- If failures rise with queue wait/backlog, also inspect worker saturation and outbound connectivity.

### `ProcessingFailuresHigh`

Meaning:
- Generic tasks on the `processing` queue are failing at a sustained elevated rate.

First checks:
- Open `Queue Operations` and inspect processing throughput/failures.
- Check whether the problem is a single task type or broad queue instability.
- Use queue wait/backlog to distinguish saturation from logic/input failures.

### `ReconciliationFailuresHigh`

Meaning:
- Reconciliation failures repeated across multiple scheduled runs.

First checks:
- Open `Publish Operations`.
- Treat this primarily as an external integration signal: platform instability, auth drift or API shape changes are more likely than Celery/routing issues.

## Saturation heuristics

- `backlog high` + `queue wait high`: strong signal of sustained saturation.
- `backlog high` + `queue wait normal`: likely transient spike or acceptable burst absorption.
- `queue wait high` + `backlog normal`: check intentional delays (`countdown`) or worker execution slowdown.
- `stage latency high` + `queue wait normal`: work itself is getting slower.
- `failure alert` + low backlog/wait: likely dependency, input or external API problem rather than queue saturation.
