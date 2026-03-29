# Observability

This document describes how logs and metrics work in this project.

## Logs

### Structured JSON

Key flows emit **one JSON object per log line** (no wrapper prefix). The payload is produced by `log_event()` in `apps/jobs/logging_utils.py` and written with `ensure_ascii=False` so Portuguese and other non-ASCII text stay readable.

Configured loggers (see `LOGGING` in `social_automation/settings.py`) use a pass-through formatter so the message body is valid JSON for ingestion (Loki, CloudWatch, etc.).

### Correlation IDs

A **correlation ID** ties related log lines together within a Celery task or flow:

- **ContextVar** (`get_correlation_id` / `set_correlation_id` / `new_correlation_id` in `apps/jobs/logging_utils.py`) holds the ID for the current execution context.
- The publish path reuses an existing ID when present: `get_correlation_id() or new_correlation_id()` so nested work shares the same value when appropriate.

Search logs by the `correlation_id` field in each JSON line.

### Typical events (non-exhaustive)

| Area | Examples |
|------|----------|
| Schedule | `schedule_run_started`, `schedule_run_finished`, `scheduled_posts_generated` |
| Transcription / render | `transcription_started`, `transcription_finished`, `render_started`, `render_finished` |
| Publish | `publish_started`, `publish_finished`, `publish_attempt_succeeded`, `publish_reconciliation_finished` |

---

## Metrics

### Prometheus (counters + histograms)

The project uses **`prometheus_client`** with minimal metrics in `apps/common/metrics.py`:

- **Transcription** (`generate_subtitles_task`): jobs, failures, duration (labels: `workload_type` = `cpu` | `gpu`).
- **Render** (`burn_subtitles_task`): jobs, failures, duration (same `workload_type`).
- **Publish** (`_run_post_to_platforms`): attempts, final failures, successful duration.
- **Reconciliation** (`reconcile_youtube_schedules_task`): runs, failures, successful duration.

### `/metrics` endpoint

The Django app exposes **`GET /metrics/`** (see `social_automation/metrics_view.py` and `urls.py`). Prometheus scrapes this URL to collect the current process registry.

### `PROMETHEUS_MULTIPROC_DIR` (Celery + web)

Celery workers run in **separate processes** from the Django web server. By default, each process has its **own** Prometheus registry, so scraping only the web process does **not** include task metrics updated inside workers.

To **aggregate** metrics from the web process and all Celery workers into a single scrape:

1. Set **`PROMETHEUS_MULTIPROC_DIR`** to a **shared writable directory** (same path in every process that imports `apps.common.metrics` and in the process serving `/metrics/`).
2. Ensure the variable is set **before** Python starts (Docker `environment`, systemd `Environment=`, etc.) so metric registration uses multiprocess mode.
3. The `/metrics/` view uses `CollectorRegistry` + `multiprocess.MultiProcessCollector` + `generate_latest(registry)` when `PROMETHEUS_MULTIPROC_DIR` is set; otherwise it scrapes the default in-process registry only.

#### Docker Compose (this repo)

`docker-compose.yml` wires this up for you:

- **`prometheus_multiproc`** named volume mounted at **`/tmp/prometheus_multiproc`** on `web`, `celery`, `celery_render`, `celery_publish`, and `beat`.
- **`PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc`** on those services. You do **not** need to set this in `.env` for Docker; add it to `.env` only if you run the app outside Compose and want the same path.
- The **`web`** service clears `/tmp/prometheus_multiproc/*` at container start (before migrate/runserver) so stale multiprocess files are reset. Celery starts after `web` is up, so workers attach to a clean directory on a fresh stack start.

See the [prometheus_client multiprocess documentation](https://github.com/prometheus/client_python#multiprocess-mode-gunicorn) for more details (e.g. worker lifecycle).

If you do **not** set `PROMETHEUS_MULTIPROC_DIR`, `/metrics/` still works but only reflects the **current process** (often mostly empty for task metrics when only the web process is scraped).

---

## Validation status

This section records what has been exercised in running environments versus what remains intentionally unvalidated.

| Area | Status |
|------|--------|
| Structured JSON logs + correlation IDs | Used in production-style runs; suitable for aggregation (Loki, CloudWatch, etc.). |
| Reconciliation metrics (`reconcile_youtube_schedules_task`) | Runtime-validated (including multiprocess scrape via `/metrics/`). |
| Publish metrics (`_run_post_to_platforms` and related counters/histograms) | **Implemented** in code; end-to-end scrape validation was **deferred** to avoid unnecessary **YouTube API quota** during ad-hoc testing. |

Re-validate publish metrics when you can afford a controlled publish or staging traffic.
