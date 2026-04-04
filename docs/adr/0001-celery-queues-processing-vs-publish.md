# ADR-0001: Two Celery queues — `processing` and `publish`

- **Date:** 2026-03-26  
- **Status:** Accepted  
- **Context:** Platform with heavy CPU/GPU work (FFmpeg, Whisper, chunked transcription) and time-sensitive I/O work (check scheduled posts, call social APIs, reconcile YouTube state).

## Problem

If all Celery tasks share one queue and a limited set of workers:

- A long transcription or *render* can **saturate** workers for minutes or hours.
- **Publishing** and **scheduling** tasks (which should run in predictable windows) sit **behind** the queue, delaying posts or checks.

## Decision

1. **`processing` queue (default)** — `CELERY_TASK_DEFAULT_QUEUE = "processing"`.  
   Targets: `process_job`, subtitles, *auto cuts* (`analyze_auto_cuts_task`, `finalizar_auto_cut_task`), and heavy cleanups where applicable.

2. **`publish` queue** — explicit routing in `CELERY_TASK_ROUTES` in `social_automation/settings.py`.  
   Targets: `check_scheduled_posts_task`, `post_to_platforms_task`, `generate_daily_factory_schedules_task`, `reconcile_youtube_schedules_task`, post-batch thumbnail upload, brand queues, etc.

3. **Operations:** in production expect **two (or more) workers** — at least one consumer `-Q processing` and another `-Q publish` (see README / `.bat` scripts).

## Consequences

**Positive**

- Publishing and scheduling **do not compete** directly with render/transcription jobs in the same worker pool when those pools are separated.
- Easier to **scale** only the queue under pressure (more *processing* vs more *publish* workers).

**Negative / cost**

- Slightly **more complex** infrastructure and operations (two worker commands, per-queue monitoring).
- If a single worker consumes **both** queues without capacity separation, the benefit shrinks — the decision assumes **separate capacity or prioritization** in operations.

## Alternatives considered

- **Single queue + Celery priorities** — possible, but more fragile when heavy task volume is high and unpredictable.
- **Per-client queues** — useful in strict multi-tenant; here the logical *tenant* is *Factory/Brand* within one deployment.

## References in code

- `social_automation/settings.py` — `CELERY_TASK_DEFAULT_QUEUE`, `CELERY_TASK_ROUTES`
- `config/celery.py` — *beat schedule* for periodic tasks
