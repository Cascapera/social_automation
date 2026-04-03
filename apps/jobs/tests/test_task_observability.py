from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.common.task_observability import (
    observe_celery_task,
    resolve_task_observation_labels,
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
        task = SimpleNamespace(
            name=task_name,
            request=SimpleNamespace(delivery_info={"routing_key": "processing"}),
        )

        with patch.multiple(
            "apps.common.task_observability",
            task_started_total=started,
            task_finished_total=finished,
            task_failed_total=failed,
            task_duration_ms=duration,
        ):
            with observe_celery_task(task=task):
                pass

        self.assertEqual(started.children[labels_key].incs, [1])
        self.assertEqual(finished.children[labels_key].incs, [1])
        self.assertNotIn(labels_key, failed.children)
        self.assertEqual(len(duration.children[labels_key].observations), 1)
        self.assertGreaterEqual(duration.children[labels_key].observations[0], 0.0)

    def test_observe_task_records_failure_metrics(self):
        task_name = "apps.social.tasks.cleanup_posted_media_task"
        labels_key = self._labels_key(task_name, "processing")
        started = _DummyMetric()
        finished = _DummyMetric()
        failed = _DummyMetric()
        duration = _DummyMetric()
        task = SimpleNamespace(
            name=task_name,
            request=SimpleNamespace(delivery_info={"routing_key": "processing"}),
        )

        with patch.multiple(
            "apps.common.task_observability",
            task_started_total=started,
            task_finished_total=finished,
            task_failed_total=failed,
            task_duration_ms=duration,
        ):
            with self.assertRaises(RuntimeError):
                with observe_celery_task(task=task):
                    raise RuntimeError("boom")

        self.assertEqual(started.children[labels_key].incs, [1])
        self.assertNotIn(labels_key, finished.children)
        self.assertEqual(failed.children[labels_key].incs, [1])
        self.assertEqual(len(duration.children[labels_key].observations), 1)
        self.assertGreaterEqual(duration.children[labels_key].observations[0], 0.0)
