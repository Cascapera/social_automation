"""Reusable Celery task observability helpers with low-cardinality labels."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from contextlib import contextmanager
from functools import wraps
from time import perf_counter, time_ns

from celery import current_task
from celery.signals import before_task_publish, task_prerun
from django.conf import settings

from .metrics import (
    queue_wait_ms,
    task_duration_ms,
    task_failed_total,
    task_finished_total,
    task_started_total,
)

TASK_ENQUEUED_AT_MS_HEADER = "x-enqueued-at-ms"
_TASK_QUEUE_WAIT_RECORDED_ATTR = "_queue_wait_ms_recorded"
_SIGNAL_HANDLERS_REGISTERED = False


def _now_ms() -> float:
    return time_ns() / 1_000_000.0


def _coerce_ms(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_task_observation_labels(
    task=None,
    *,
    task_name: str | None = None,
    queue_name: str | None = None,
) -> tuple[str, str]:
    """Resolve stable task labels from the active Celery task/request."""
    task_obj = task or current_task
    resolved_task_name = (
        (task_name or "").strip()
        or str(getattr(task_obj, "name", "") or "").strip()
        or "unknown_task"
    )

    resolved_queue_name = (queue_name or "").strip()
    if not resolved_queue_name:
        request = getattr(task_obj, "request", None)
        delivery_info = getattr(request, "delivery_info", None) or {}
        resolved_queue_name = str(
            delivery_info.get("routing_key") or delivery_info.get("queue") or ""
        ).strip()

    if not resolved_queue_name:
        task_routes = getattr(settings, "CELERY_TASK_ROUTES", {}) or {}
        route = task_routes.get(resolved_task_name)
        if isinstance(route, dict):
            resolved_queue_name = str(route.get("queue") or "").strip()

    if not resolved_queue_name:
        resolved_queue_name = str(
            getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "") or "celery"
        ).strip()

    return resolved_task_name, resolved_queue_name


def set_task_enqueue_timestamp(
    *,
    headers: MutableMapping | None = None,
    properties: MutableMapping | None = None,
    enqueued_at_ms: float | None = None,
) -> bool:
    """Attach the current publish timestamp to outgoing Celery message metadata."""
    target = headers if isinstance(headers, MutableMapping) else None
    if target is None and isinstance(properties, MutableMapping):
        target = properties
    if target is None:
        return False
    target[TASK_ENQUEUED_AT_MS_HEADER] = (
        f"{enqueued_at_ms if enqueued_at_ms is not None else _now_ms():.3f}"
    )
    return True


def resolve_task_enqueue_timestamp_ms(
    task=None,
    *,
    request=None,
    headers: Mapping | None = None,
    properties: Mapping | None = None,
) -> float | None:
    """Resolve the enqueue timestamp from Celery request metadata."""
    task_obj = task or current_task
    request_obj = request or getattr(task_obj, "request", None)
    candidates: list[Mapping] = []
    for mapping in (
        headers,
        getattr(request_obj, "headers", None),
        properties,
        getattr(request_obj, "properties", None),
    ):
        if isinstance(mapping, Mapping):
            candidates.append(mapping)

    for mapping in candidates:
        resolved = _coerce_ms(mapping.get(TASK_ENQUEUED_AT_MS_HEADER))
        if resolved is not None:
            return resolved
    return None


def observe_task_queue_wait(
    task=None,
    *,
    task_name: str | None = None,
    queue_name: str | None = None,
    request=None,
    headers: Mapping | None = None,
    properties: Mapping | None = None,
    enqueued_at_ms: float | None = None,
    started_at_ms: float | None = None,
) -> float | None:
    """
    Observe queue wait time when an enqueue timestamp is available.

    This is approximate because enqueue and start timestamps are wall-clock values
    emitted from different processes.
    """
    task_obj = task or current_task
    request_obj = request or getattr(task_obj, "request", None)
    if request_obj is not None and getattr(request_obj, _TASK_QUEUE_WAIT_RECORDED_ATTR, False):
        return None

    resolved_enqueued_at_ms = enqueued_at_ms
    if resolved_enqueued_at_ms is None:
        resolved_enqueued_at_ms = resolve_task_enqueue_timestamp_ms(
            task=task_obj,
            request=request_obj,
            headers=headers,
            properties=properties,
        )
    if resolved_enqueued_at_ms is None:
        return None

    resolved_task_name, resolved_queue_name = resolve_task_observation_labels(
        task=task_obj,
        task_name=task_name,
        queue_name=queue_name,
    )
    wait_ms = max(
        0.0,
        (started_at_ms if started_at_ms is not None else _now_ms()) - resolved_enqueued_at_ms,
    )
    queue_wait_ms.labels(
        task_name=resolved_task_name,
        queue_name=resolved_queue_name,
    ).observe(wait_ms)
    if request_obj is not None:
        setattr(request_obj, _TASK_QUEUE_WAIT_RECORDED_ATTR, True)
    return wait_ms


@contextmanager
def observe_celery_task(task=None, *, task_name: str | None = None, queue_name: str | None = None):
    """
    Emit generic task-level metrics.

    Duration is observed for the full runtime regardless of success/failure.
    """
    resolved_task_name, resolved_queue_name = resolve_task_observation_labels(
        task=task,
        task_name=task_name,
        queue_name=queue_name,
    )
    observe_task_queue_wait(
        task=task,
        task_name=resolved_task_name,
        queue_name=resolved_queue_name,
    )
    labels = {
        "task_name": resolved_task_name,
        "queue_name": resolved_queue_name,
    }
    task_started_total.labels(**labels).inc()
    started_at = perf_counter()
    try:
        yield labels
    except Exception:
        task_failed_total.labels(**labels).inc()
        raise
    else:
        task_finished_total.labels(**labels).inc()
    finally:
        task_duration_ms.labels(**labels).observe((perf_counter() - started_at) * 1000.0)


def instrument_celery_task(func):
    """Decorator for Celery task entrypoints."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        task_obj = args[0] if args and hasattr(args[0], "request") else current_task
        task_name = f"{func.__module__}.{func.__name__}"
        with observe_celery_task(task=task_obj, task_name=task_name):
            return func(*args, **kwargs)

    return wrapper


def _handle_before_task_publish(headers=None, properties=None, **kwargs) -> None:
    set_task_enqueue_timestamp(headers=headers, properties=properties)


def _handle_task_prerun(sender=None, task=None, **kwargs) -> None:
    task_obj = task if getattr(task, "request", None) is not None else None
    if task_obj is None and getattr(sender, "request", None) is not None:
        task_obj = sender
    if task_obj is None:
        return
    observe_task_queue_wait(task=task_obj)


def register_celery_observability_signal_handlers() -> None:
    """Register lightweight Celery signal hooks for enqueue/start observability."""
    global _SIGNAL_HANDLERS_REGISTERED
    if _SIGNAL_HANDLERS_REGISTERED:
        return
    before_task_publish.connect(_handle_before_task_publish, weak=False)
    task_prerun.connect(_handle_task_prerun, weak=False)
    _SIGNAL_HANDLERS_REGISTERED = True
