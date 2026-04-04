"""Lightweight Celery backlog observability for Redis-backed queues."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from django.conf import settings
from redis import Redis

logger = logging.getLogger(__name__)

QUEUE_BACKLOG_METRIC_NAME = "queue_backlog"
QUEUE_BACKLOG_METRIC_DESCRIPTION = (
    "Approximate ready-task backlog per Celery queue collected from Redis LLEN"
)


def get_observed_queue_names() -> tuple[str, ...]:
    """Return a stable, low-cardinality list of queues to observe."""
    queue_names: list[str] = []

    def _append(raw_value) -> None:
        value = str(raw_value or "").strip()
        if value and value not in queue_names:
            queue_names.append(value)

    _append(getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", ""))
    _append(getattr(settings, "CELERY_QUEUE_TRANSCRIPTION", ""))
    _append(getattr(settings, "CELERY_QUEUE_RENDER", ""))

    task_routes = getattr(settings, "CELERY_TASK_ROUTES", {}) or {}
    for route in task_routes.values():
        if isinstance(route, dict):
            _append(route.get("queue"))

    return tuple(queue_names or ("celery",))


def _is_redis_broker_url(broker_url: str) -> bool:
    return broker_url.lower().startswith(("redis://", "rediss://", "unix://"))


def collect_queue_backlog_samples(
    *,
    broker_url: str | None = None,
    queue_names: Iterable[str] | None = None,
    redis_client=None,
) -> dict[str, int]:
    """
    Collect per-queue ready backlog from Redis.

    For the Redis transport this measures the main ready queue size via ``LLEN``.
    It does not include reserved/unacked tasks.
    """
    resolved_broker_url = str(
        broker_url or getattr(settings, "CELERY_BROKER_URL", "") or ""
    ).strip()
    if not resolved_broker_url or not _is_redis_broker_url(resolved_broker_url):
        return {}

    resolved_queue_names = tuple(queue_names or get_observed_queue_names())
    if not resolved_queue_names:
        return {}

    client = redis_client
    created_client = False
    if client is None:
        try:
            client = Redis.from_url(
                resolved_broker_url,
                socket_connect_timeout=1,
                socket_timeout=1,
                retry_on_timeout=False,
            )
            created_client = True
        except Exception:
            logger.warning("queue_backlog_client_init_failed", exc_info=True)
            return {}

    backlog_by_queue: dict[str, int] = {}
    try:
        for queue_name in resolved_queue_names:
            try:
                backlog_by_queue[queue_name] = max(0, int(client.llen(queue_name) or 0))
            except Exception:
                logger.warning(
                    "queue_backlog_queue_collection_failed",
                    extra={"queue_name": queue_name},
                    exc_info=True,
                )
    finally:
        if created_client:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    return backlog_by_queue


class QueueBacklogCollector:
    """Prometheus collector for Redis queue backlog."""

    def __init__(
        self,
        *,
        broker_url: str | None = None,
        queue_names: Iterable[str] | None = None,
        backlog_collector=collect_queue_backlog_samples,
    ) -> None:
        self._broker_url = broker_url
        self._queue_names = tuple(queue_names) if queue_names is not None else None
        self._backlog_collector = backlog_collector

    def collect(self):
        from prometheus_client.core import GaugeMetricFamily

        metric = GaugeMetricFamily(
            QUEUE_BACKLOG_METRIC_NAME,
            QUEUE_BACKLOG_METRIC_DESCRIPTION,
            labels=["queue_name"],
        )
        for queue_name, backlog in self._backlog_collector(
            broker_url=self._broker_url,
            queue_names=self._queue_names,
        ).items():
            metric.add_metric([queue_name], backlog)
        yield metric
