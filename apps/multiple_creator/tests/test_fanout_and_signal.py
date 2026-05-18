"""Tests da Fase 6: fanout, signal de fechamento, retry granular."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.auto_cuts.models import AutoCutAnalysis
from apps.brands.models import Brand, Factory
from apps.mediahub.models import SourceVideo
from apps.multiple_creator.models import (
    MultipleCreatorBrandExecution,
    MultipleCreatorJob,
)
from apps.multiple_creator.tasks import multiple_creator_fanout_task

User = get_user_model()


def _fake_segments(n: int = 3) -> list[dict]:
    return [
        {"start": i * 5.0, "end": (i + 1) * 5.0, "text": f"seg {i}"}
        for i in range(n)
    ]


class _BaseMcCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mc-fan", password="x")
        self.factory = Factory.objects.create(name="F")
        self.brand_a = Brand.objects.create(name="A", slug="a", factory=self.factory)
        self.brand_b = Brand.objects.create(name="B", slug="b", factory=self.factory)
        self.brand_c = Brand.objects.create(name="C", slug="c", factory=self.factory)

    def _make_job(self, *, status="READY", source=None, with_transcript=True, brands=None):
        brands = brands or [self.brand_a, self.brand_b]
        job = MultipleCreatorJob.objects.create(
            user=self.user,
            source_kind="SOURCE" if source else "YOUTUBE",
            source=source,
            youtube_url="" if source else "https://yt/abc",
            status=status,
            transcript_segments=_fake_segments(3) if with_transcript else None,
            transcript="seg 0 seg 1 seg 2" if with_transcript else "",
            assunto="tema",
            name="live",
            prompt_version="viral",
        )
        for b in brands:
            MultipleCreatorBrandExecution.objects.create(job=job, brand=b)
        return job


class MultipleCreatorFanoutTaskTests(_BaseMcCase):
    """multiple_creator_fanout_task cria filhas e enfileira analise."""

    @patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay")
    def test_fanout_creates_one_autocut_per_brand(self, mock_delay):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a, self.brand_b, self.brand_c])
        multiple_creator_fanout_task(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, "RUNNING_BRANDS")
        executions = list(job.brand_executions.all().order_by("id"))
        self.assertEqual(len(executions), 3)
        for ex in executions:
            self.assertEqual(ex.status, "ANALYZING")
            self.assertIsNotNone(ex.auto_cut_analysis_id)
            self.assertIsNotNone(ex.started_at)
            analysis = AutoCutAnalysis.objects.get(pk=ex.auto_cut_analysis_id)
            self.assertEqual(analysis.target_brand_id, ex.brand_id)
            self.assertEqual(analysis.brand_id, ex.brand_id)
            self.assertEqual(len(analysis.transcript_segments or []), 3)
            self.assertEqual(analysis.source_id, source.id)
        self.assertEqual(mock_delay.call_count, 3)

    @patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay")
    def test_fanout_idempotent_when_not_ready(self, mock_delay):
        job = self._make_job(status="RUNNING_BRANDS")
        multiple_creator_fanout_task(job.id)
        mock_delay.assert_not_called()
        # Executions seguem PENDING
        self.assertTrue(all(ex.status == "PENDING" for ex in job.brand_executions.all()))

    @patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay")
    def test_fanout_with_no_pending_marks_done(self, mock_delay):
        job = self._make_job()
        # marcar todas como DONE — fanout nao deve spawnar
        job.brand_executions.update(status="DONE")
        multiple_creator_fanout_task(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, "DONE")
        mock_delay.assert_not_called()


class MultipleCreatorSignalTests(_BaseMcCase):
    """post_save em AutoCutAnalysis fecha BrandExecution e agrega job."""

    def _link_analysis(self, execution, **overrides):
        defaults = dict(
            user=self.user,
            brand=execution.brand,
            target_brand=execution.brand,
            prompt_version="viral",
            transcript_segments=_fake_segments(2),
        )
        defaults.update(overrides)
        analysis = AutoCutAnalysis.objects.create(**defaults)
        execution.auto_cut_analysis = analysis
        execution.status = "ANALYZING"
        execution.save(update_fields=["auto_cut_analysis", "status"])
        return analysis

    def test_done_closes_brand_execution(self):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a])
        execution = job.brand_executions.first()
        analysis = self._link_analysis(execution)
        analysis.status = "done"
        analysis.save(update_fields=["status"])
        execution.refresh_from_db()
        self.assertEqual(execution.status, "DONE")
        self.assertIsNotNone(execution.finished_at)
        job.refresh_from_db()
        self.assertEqual(job.status, "DONE")

    def test_error_marks_brand_execution_error(self):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a])
        execution = job.brand_executions.first()
        analysis = self._link_analysis(execution)
        analysis.status = "error"
        analysis.error = "Grok failed"
        analysis.save(update_fields=["status", "error"])
        execution.refresh_from_db()
        self.assertEqual(execution.status, "ERROR")
        self.assertIn("Grok", execution.error)
        job.refresh_from_db()
        self.assertEqual(job.status, "ERROR")

    def test_mixed_done_and_error_yields_partial(self):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a, self.brand_b])
        e_a, e_b = list(job.brand_executions.order_by("id"))
        a_a = self._link_analysis(e_a)
        a_b = self._link_analysis(e_b)
        a_a.status = "done"
        a_a.save(update_fields=["status"])
        a_b.status = "error"
        a_b.save(update_fields=["status"])
        job.refresh_from_db()
        self.assertEqual(job.status, "PARTIAL")

    def test_one_finished_other_pending_keeps_running(self):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a, self.brand_b])
        e_a, _ = list(job.brand_executions.order_by("id"))
        analysis = self._link_analysis(e_a)
        analysis.status = "done"
        analysis.save(update_fields=["status"])
        job.refresh_from_db()
        self.assertEqual(job.status, "RUNNING_BRANDS")

    def test_pending_save_does_not_trigger(self):
        """Save com status nao terminal nao fecha a execution."""
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a])
        execution = job.brand_executions.first()
        analysis = self._link_analysis(execution)
        analysis.progress_message = "ainda processando"
        analysis.save(update_fields=["progress_message"])
        execution.refresh_from_db()
        self.assertEqual(execution.status, "ANALYZING")


class MultipleCreatorRetryEndpointTests(_BaseMcCase):
    """POST /multiple-creator/<id>/retry/?brand_id=X dispara reanalise."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay")
    def test_retry_resets_errored_execution_and_dispatches(self, mock_delay):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a])
        execution = job.brand_executions.first()
        execution.status = "ERROR"
        execution.error = "anterior"
        execution.save(update_fields=["status", "error"])
        job.status = "ERROR"
        job.save(update_fields=["status"])

        res = self.client.post(
            f"/api/multiple-creator/{job.id}/retry/?brand_id={self.brand_a.id}"
        )
        self.assertEqual(res.status_code, drf_status.HTTP_200_OK, res.data)
        execution.refresh_from_db()
        self.assertEqual(execution.status, "ANALYZING")
        self.assertEqual(execution.error, "")
        self.assertIsNotNone(execution.auto_cut_analysis_id)
        mock_delay.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.status, "RUNNING_BRANDS")

    def test_retry_without_transcript_returns_400(self):
        job = self._make_job(with_transcript=False, brands=[self.brand_a])
        res = self.client.post(
            f"/api/multiple-creator/{job.id}/retry/?brand_id={self.brand_a.id}"
        )
        self.assertEqual(res.status_code, drf_status.HTTP_400_BAD_REQUEST)

    def test_retry_without_brand_id_returns_400(self):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a])
        res = self.client.post(f"/api/multiple-creator/{job.id}/retry/")
        self.assertEqual(res.status_code, drf_status.HTTP_400_BAD_REQUEST)

    def test_retry_brand_not_in_job_returns_404(self):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a])
        res = self.client.post(
            f"/api/multiple-creator/{job.id}/retry/?brand_id={self.brand_c.id}"
        )
        self.assertEqual(res.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_retry_while_running_returns_conflict(self):
        source = SourceVideo.objects.create(brand=self.brand_a)
        job = self._make_job(source=source, brands=[self.brand_a])
        ex = job.brand_executions.first()
        ex.status = "ANALYZING"
        ex.save(update_fields=["status"])
        res = self.client.post(
            f"/api/multiple-creator/{job.id}/retry/?brand_id={self.brand_a.id}"
        )
        self.assertEqual(res.status_code, drf_status.HTTP_409_CONFLICT)
