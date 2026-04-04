from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.common.metrics import queue_wait_ms
from apps.common.task_observability import (
    TASK_ENQUEUED_AT_MS_HEADER,
    observe_celery_task,
    observe_task_queue_wait,
    resolve_task_observation_labels,
    set_task_enqueue_timestamp,
)


class _DummyMetricChild:
    def __init__(self) -> None:
        self.incs: list[int] = []
        self.observations: list[float] = []

    def inc(self, amount: int = 1) -> None:
        self.incs.append(amount)

    def observe(self, value: float) -> None:
        self.observations.append(value)


class _DummyMetric:
    def __init__(self) -> None:
        self.children: dict[tuple[tuple[str, str], ...], _DummyMetricChild] = {}

    def labels(self, **labels):
        key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        child = self.children.get(key)
        if child is None:
            child = _DummyMetricChild()
            self.children[key] = child
        return child


class TaskObservabilityTests(SimpleTestCase):
    def _labels_key(self, task_name: str, queue_name: str):
        return (
            ("queue_name", queue_name),
            ("task_name", task_name),
        )

    @override_settings(CELERY_TASK_DEFAULT_QUEUE="processing")
    def test_resolve_labels_uses_delivery_info_queue(self):
        task = SimpleNamespace(
            name="apps.cuts.tasks.extract_cuts_task",
            request=SimpleNamespace(delivery_info={"routing_key": "processing"}),
        )

        self.assertEqual(
            resolve_task_observation_labels(task=task),
            ("apps.cuts.tasks.extract_cuts_task", "processing"),
        )

    @override_settings(
        CELERY_TASK_DEFAULT_QUEUE="processing",
        CELERY_TASK_ROUTES={
            "apps.social.tasks.cleanup_posted_media_task": {"queue": "processing"},
        },
    )
    def test_resolve_labels_falls_back_to_task_routes(self):
        task = SimpleNamespace(
            name="apps.social.tasks.cleanup_posted_media_task",
            request=SimpleNamespace(delivery_info={}),
        )

        self.assertEqual(
            resolve_task_observation_labels(task=task),
            ("apps.social.tasks.cleanup_posted_media_task", "processing"),
        )

    def test_observe_task_records_success_metrics(self):
        task_name = "apps.jobs.tasks_auto_fetch.check_and_fetch_new_videos_task"
        labels_key = self._labels_key(task_name, "processing")
        started = _DummyMetric()
        finished = _DummyMetric()
        failed = _DummyMetric()
        duration = _DummyMetric()
        queue_wait = _DummyMetric()
        task = SimpleNamespace(
            name=task_name,
            request=SimpleNamespace(
                delivery_info={"routing_key": "processing"},
                headers={TASK_ENQUEUED_AT_MS_HEADER: "1000"},
            ),
        )

        with patch.multiple(
            "apps.common.task_observability",
            task_started_total=started,
            task_finished_total=finished,
            task_failed_total=failed,
            task_duration_ms=duration,
            queue_wait_ms=queue_wait,
        ), patch("apps.common.task_observability._now_ms", return_value=2600.0):
            with observe_celery_task(task=task):
                pass

        self.assertEqual(started.children[labels_key].incs, [1])
        self.assertEqual(finished.children[labels_key].incs, [1])
        self.assertNotIn(labels_key, failed.children)
        self.assertEqual(len(duration.children[labels_key].observations), 1)
        self.assertGreaterEqual(duration.children[labels_key].observations[0], 0.0)
        self.assertEqual(queue_wait.children[labels_key].observations, [1600.0])

    def test_observe_task_records_failure_metrics(self):
        task_name = "apps.social.tasks.cleanup_posted_media_task"
        labels_key = self._labels_key(task_name, "processing")
        started = _DummyMetric()
        finished = _DummyMetric()
        failed = _DummyMetric()
        duration = _DummyMetric()
        queue_wait = _DummyMetric()
        task = SimpleNamespace(
            name=task_name,
            request=SimpleNamespace(
                delivery_info={"routing_key": "processing"},
                headers={TASK_ENQUEUED_AT_MS_HEADER: "500"},
            ),
        )

        with patch.multiple(
            "apps.common.task_observability",
            task_started_total=started,
            task_finished_total=finished,
            task_failed_total=failed,
            task_duration_ms=duration,
            queue_wait_ms=queue_wait,
        ):
            with self.assertRaises(RuntimeError):
                with observe_celery_task(task=task):
                    raise RuntimeError("boom")

        self.assertEqual(started.children[labels_key].incs, [1])
        self.assertNotIn(labels_key, finished.children)
        self.assertEqual(failed.children[labels_key].incs, [1])
        self.assertEqual(len(duration.children[labels_key].observations), 1)
        self.assertGreaterEqual(duration.children[labels_key].observations[0], 0.0)
        self.assertEqual(len(queue_wait.children[labels_key].observations), 1)

    def test_set_task_enqueue_timestamp_prefers_headers_and_overwrites_stale_value(self):
        headers = {TASK_ENQUEUED_AT_MS_HEADER: "100.0"}
        properties = {}

        self.assertTrue(
            set_task_enqueue_timestamp(
                headers=headers,
                properties=properties,
                enqueued_at_ms=1234.5,
            )
        )
        self.assertEqual(headers[TASK_ENQUEUED_AT_MS_HEADER], "1234.500")
        self.assertNotIn(TASK_ENQUEUED_AT_MS_HEADER, properties)

    def test_observe_task_queue_wait_is_recorded_once_per_request(self):
        task_name = "apps.jobs.tasks.process_job"
        labels_key = self._labels_key(task_name, "render")
        queue_wait = _DummyMetric()
        task = SimpleNamespace(
            name=task_name,
            request=SimpleNamespace(
                delivery_info={"routing_key": "render"},
                headers={TASK_ENQUEUED_AT_MS_HEADER: "1000"},
            ),
        )

        with patch(
            "apps.common.task_observability.queue_wait_ms",
            queue_wait,
        ), patch("apps.common.task_observability._now_ms", return_value=2500.0):
            observe_task_queue_wait(task=task)
            observe_task_queue_wait(task=task)

        self.assertEqual(queue_wait.children[labels_key].observations, [1500.0])

    def test_queue_wait_metric_uses_low_cardinality_labels(self):
        self.assertEqual(tuple(queue_wait_ms._labelnames), ("task_name", "queue_name"))
