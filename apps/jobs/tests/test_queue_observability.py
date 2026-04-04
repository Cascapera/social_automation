from __future__ import annotations

import json
import re
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from apps.common.queue_backlog import (
    QUEUE_BACKLOG_METRIC_NAME,
    QueueBacklogCollector,
    collect_queue_backlog_samples,
    get_observed_queue_names,
)


class _FakeRedisClient:
    def __init__(self, backlog_by_queue: dict[str, int]) -> None:
        self._backlog_by_queue = backlog_by_queue
        self.closed = False

    def llen(self, queue_name: str) -> int:
        return self._backlog_by_queue.get(queue_name, 0)

    def close(self) -> None:
        self.closed = True


class QueueBacklogObservabilityTests(SimpleTestCase):
    @override_settings(
        CELERY_TASK_DEFAULT_QUEUE="processing",
        CELERY_QUEUE_TRANSCRIPTION="transcription",
        CELERY_QUEUE_RENDER="render",
        CELERY_TASK_ROUTES={
            "apps.social.tasks.post_to_platforms_task": {"queue": "publish"},
            "apps.jobs.tasks.generate_subtitles_task": {"queue": "transcription"},
            "apps.jobs.tasks.burn_subtitles_task": {"queue": "render"},
        },
    )
    def test_get_observed_queue_names_uses_settings_and_routes(self):
        self.assertEqual(
            get_observed_queue_names(),
            ("processing", "transcription", "render", "publish"),
        )

    @override_settings(CELERY_BROKER_URL="redis://127.0.0.1:6379/0")
    def test_collect_queue_backlog_samples_reads_known_queues(self):
        client = _FakeRedisClient(
            {
                "processing": 2,
                "transcription": 5,
                "render": 1,
                "publish": 0,
            }
        )

        self.assertEqual(
            collect_queue_backlog_samples(
                redis_client=client,
                queue_names=("processing", "transcription", "render", "publish"),
            ),
            {
                "processing": 2,
                "transcription": 5,
                "render": 1,
                "publish": 0,
            },
        )
        self.assertFalse(client.closed)

    @override_settings(CELERY_BROKER_URL="amqp://guest:guest@localhost//")
    def test_collect_queue_backlog_samples_is_noop_for_non_redis_broker(self):
        self.assertEqual(
            collect_queue_backlog_samples(
                queue_names=("processing", "transcription", "render", "publish"),
            ),
            {},
        )

    def test_queue_backlog_collector_emits_queue_backlog_metric(self):
        collector = QueueBacklogCollector(
            backlog_collector=lambda **kwargs: {"processing": 3, "publish": 1}
        )

        metric = next(collector.collect())
        samples = {
            (sample.labels["queue_name"], sample.value)
            for sample in metric.samples
            if sample.name == QUEUE_BACKLOG_METRIC_NAME
        }

        self.assertEqual(metric.name, QUEUE_BACKLOG_METRIC_NAME)
        self.assertEqual(samples, {("processing", 3), ("publish", 1)})
        self.assertTrue(
            all(set(sample.labels.keys()) == {"queue_name"} for sample in metric.samples)
        )


class QueueDashboardQueriesTests(SimpleTestCase):
    def test_queue_dashboard_queries_only_use_real_metrics(self):
        dashboard_path = (
            Path(__file__).resolve().parents[3]
            / "monitoring"
            / "grafana"
            / "dashboards"
            / "dashboard_02_queues.json"
        )
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        expressions = [
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
            if target.get("expr")
        ]
        metric_names = set()
        metric_pattern = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?=\{|\[|$)")
        for expression in expressions:
            metric_names.update(metric_pattern.findall(expression))

        self.assertEqual(
            metric_names,
            {
                "publish_attempts_total",
                "publish_failures_total",
                "publish_reconciliation_failures_total",
                "publish_reconciliation_runs_total",
                "queue_backlog",
                "queue_wait_ms_bucket",
                "queue_wait_ms_count",
                "queue_wait_ms_sum",
                "render_failures_total",
                "render_jobs_total",
                "task_failed_total",
                "task_started_total",
                "transcription_failures_total",
                "transcription_jobs_total",
            },
        )
