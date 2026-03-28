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
