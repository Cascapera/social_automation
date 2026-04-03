"""Reusable Celery task observability helpers with low-cardinality labels."""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from time import perf_counter

from celery import current_task
from django.conf import settings

from .metrics import (
    task_duration_ms,
    task_failed_total,
    task_finished_total,
    task_started_total,
)


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
