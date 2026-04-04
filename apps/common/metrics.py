"""Minimal Prometheus metrics (counters + histograms). Low-cardinality labels only.

Celery tasks update metrics in worker processes. To expose them via Django ``/metrics/``,
set ``PROMETHEUS_MULTIPROC_DIR`` to a writable directory shared by web and workers
(see prometheus_client multiprocess mode).
"""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram
except ImportError:  # e.g. image not rebuilt after requirements.txt change

    class _NoOpChild:
        def inc(self, amount: int = 1) -> None:
            pass

        def observe(self, value: float) -> None:
            pass

    class _NoOpMetric:
        def labels(self, *args, **kwargs):
            return _NoOpChild()

        def inc(self, amount: int = 1) -> None:
            pass

    def Counter(*args, **kwargs):
        return _NoOpMetric()

    def Histogram(*args, **kwargs):
        return _NoOpMetric()

# Buckets for durations stored as milliseconds (observed values are ms).
_DURATION_MS_BUCKETS = (
    100.0,
    500.0,
    1_000.0,
    5_000.0,
    30_000.0,
    60_000.0,
    300_000.0,
    600_000.0,
    1_800_000.0,
    float("inf"),
)

_workload = ("workload_type",)
_task = ("task_name", "queue_name")
_grok_request = ("model", "operation")
_grok_token = ("model", "type")

# --- Generic Celery task observability ---
task_started_total = Counter(
    "task_started_total",
    "Celery task executions started",
    _task,
)
task_finished_total = Counter(
    "task_finished_total",
    "Celery task executions finished successfully",
    _task,
)
task_failed_total = Counter(
    "task_failed_total",
    "Celery task executions failed with exception",
    _task,
)
task_duration_ms = Histogram(
    "task_duration_ms",
    "Celery task runtime in milliseconds",
    _task,
    buckets=_DURATION_MS_BUCKETS,
)
queue_wait_ms = Histogram(
    "queue_wait_ms",
    "Approximate Celery queue wait time in milliseconds (enqueue to task start)",
    _task,
    buckets=_DURATION_MS_BUCKETS,
)

# --- Transcription ---
transcription_jobs_total = Counter(
    "transcription_jobs_total",
    "Transcription jobs started (after validation)",
    _workload,
)
transcription_failures_total = Counter(
    "transcription_failures_total",
    "Transcription failures (exception during generate_subtitles)",
    _workload,
)
transcription_duration_ms = Histogram(
    "transcription_duration_ms",
    "Transcription duration in milliseconds",
    _workload,
    buckets=_DURATION_MS_BUCKETS,
)

# --- Render (burn subtitles task) ---
render_jobs_total = Counter(
    "render_jobs_total",
    "Render (burn subtitles) jobs started",
    _workload,
)
render_failures_total = Counter(
    "render_failures_total",
    "Render (burn subtitles) failures",
    _workload,
)
render_duration_ms = Histogram(
    "render_duration_ms",
    "Render (burn subtitles) duration in milliseconds",
    _workload,
    buckets=_DURATION_MS_BUCKETS,
)

# --- Publish (YouTube / multi-platform via _run_post_to_platforms) ---
publish_attempts_total = Counter(
    "publish_attempts_total",
    "Publish runs started (publish_started)",
)
publish_failures_total = Counter(
    "publish_failures_total",
    "Publish final failures (status FAILED)",
)
publish_quota_exhaustion_attempts_total = Counter(
    "publish_quota_exhaustion_attempts_total",
    "YouTube quotaExceeded handling (each reschedule or final failure after retries)",
)
publish_duration_ms = Histogram(
    "publish_duration_ms",
    "Successful publish duration in milliseconds",
    buckets=_DURATION_MS_BUCKETS,
)

# --- YouTube schedule reconciliation ---
publish_reconciliation_runs_total = Counter(
    "publish_reconciliation_runs_total",
    "reconcile_youtube_schedules_task runs started",
)
publish_reconciliation_failures_total = Counter(
    "publish_reconciliation_failures_total",
    "reconcile_youtube_schedules_task uncaught exceptions",
)
publish_reconciliation_duration_ms = Histogram(
    "publish_reconciliation_duration_ms",
    "Successful reconciliation run duration in milliseconds",
    buckets=_DURATION_MS_BUCKETS,
)

# --- Grok / xAI cost observability ---
grok_requests_total = Counter(
    "grok_requests_total",
    "Grok API requests attempted",
    _grok_request,
)
grok_tokens_total = Counter(
    "grok_tokens_total",
    "Grok token usage by direction",
    _grok_token,
)
grok_cost_usd_total = Counter(
    "grok_cost_usd_total",
    "Estimated Grok API cost in USD",
    ("model",),
)
grok_request_duration_ms = Histogram(
    "grok_request_duration_ms",
    "Grok API request duration in milliseconds",
    ("model",),
    buckets=_DURATION_MS_BUCKETS,
)
