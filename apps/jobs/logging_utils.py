"""Structured logging helpers for observability.

Usage:
    from apps.jobs.logging_utils import log_event

    log_event(
        logger,
        event="schedule_run_started",
        correlation_id=cid,
        factory_id=factory.id,
        status="started",
    )
"""
from __future__ import annotations

import json
import logging
import time as _time
import uuid
from contextvars import ContextVar
from typing import Any

# ---------------------------------------------------------------------------
# Correlation-ID context variable
# Stored per-thread/async-task via contextvars so it propagates naturally
# within a single Celery task execution without any middleware.
# ---------------------------------------------------------------------------
_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    return _correlation_id_var.get()


def set_correlation_id(cid: str) -> None:
    _correlation_id_var.set(cid)


def new_correlation_id() -> str:
    """Generate a new UUID4 correlation ID and store it in the current context."""
    cid = uuid.uuid4().hex
    set_correlation_id(cid)
    return cid


def ensure_job_correlation_id(job) -> str:
    """Return a stable correlation ID for this Job (persisted on the row).

    The first Celery task that touches the job creates the ID; transcription, render,
    and pipeline tasks reuse it so logs share one ``correlation_id`` end-to-end.

    Sets the ContextVar so ``log_event()`` without an explicit ID uses the same value.
    """
    from django.db import transaction

    from apps.jobs.models import Job

    with transaction.atomic():
        locked = Job.objects.select_for_update().get(pk=job.pk)
        if locked.correlation_id:
            cid = locked.correlation_id
        else:
            cid = uuid.uuid4().hex
            locked.correlation_id = cid
            locked.save(update_fields=["correlation_id"])
    job.correlation_id = cid
    set_correlation_id(cid)
    return cid


def resolve_scheduled_post_correlation_id(post) -> str:
    """Correlation ID for publish: reuse Job ID when the post is tied to a Job; otherwise persist on ScheduledPost."""
    from django.db import transaction

    from apps.jobs.models import Job, ScheduledPost

    if post.job_id:
        job = post.job
        if job is None:
            job = Job.objects.get(pk=post.job_id)
        return ensure_job_correlation_id(job)

    with transaction.atomic():
        locked = ScheduledPost.objects.select_for_update().get(pk=post.pk)
        if locked.correlation_id:
            cid = locked.correlation_id
        else:
            cid = uuid.uuid4().hex
            locked.correlation_id = cid
            locked.save(update_fields=["correlation_id"])
    post.correlation_id = cid
    set_correlation_id(cid)
    return cid


# ---------------------------------------------------------------------------
# Structured log emitter
# ---------------------------------------------------------------------------

def log_event(
    logger: logging.Logger,
    *,
    event: str,
    correlation_id: str | None = None,
    task_id: str | None = None,
    factory_id: int | None = None,
    schedule_run_id: int | None = None,
    number_of_posts: int | None = None,
    status: str | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
    **extra: Any,
) -> None:
    """Emit a single structured JSON log line.

    All None values are omitted from the payload to keep logs compact.
    """
    payload: dict[str, Any] = {"event": event}

    cid = correlation_id if correlation_id is not None else get_correlation_id()
    if cid:
        payload["correlation_id"] = cid
    if task_id is not None:
        payload["task_id"] = task_id
    if factory_id is not None:
        payload["factory_id"] = factory_id
    if schedule_run_id is not None:
        payload["schedule_run_id"] = schedule_run_id
    if number_of_posts is not None:
        payload["number_of_posts"] = number_of_posts
    if status is not None:
        payload["status"] = status
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 2)
    if error is not None:
        payload["error"] = error
    payload.update(extra)

    level = logging.ERROR if status == "error" else logging.INFO
    logger.log(level, json.dumps(payload, ensure_ascii=False))


# Backward-compatible alias kept so old imports don't break during the transition.
log_schedule_event = log_event


# ---------------------------------------------------------------------------
# Simple wall-clock timer
# ---------------------------------------------------------------------------

class Timer:
    """Lightweight wall-clock timer.

    Usage:
        t = Timer()
        ...
        elapsed = t.elapsed_ms()
    """

    def __init__(self) -> None:
        self._start = _time.monotonic()

    def elapsed_ms(self) -> float:
        return (_time.monotonic() - self._start) * 1000
