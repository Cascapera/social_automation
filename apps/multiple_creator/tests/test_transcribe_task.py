"""Tests da Fase 5: multiple_creator_transcribe_task + curto-circuito."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.auto_cuts.models import AutoCutAnalysis
from apps.auto_cuts.tasks import _was_transcript_prepopulated_by_multi_creator
from apps.brands.models import Brand, Factory
from apps.mediahub.models import SourceVideo
from apps.multiple_creator.models import (
    MultipleCreatorBrandExecution,
    MultipleCreatorJob,
)
from apps.multiple_creator.tasks import multiple_creator_transcribe_task

User = get_user_model()


def _fake_segments(n: int = 3) -> list[dict]:
    return [
        {"start": i * 5.0, "end": (i + 1) * 5.0, "text": f"segment {i}"}
        for i in range(n)
    ]


class MultipleCreatorTranscribeTaskTests(TestCase):
    """Comportamento da transcrição única no Multiple-Creator (Fase 5)."""

    def setUp(self):
        self.user = User.objects.create_user(username="mc-task", password="x")
        self.factory = Factory.objects.create(name="F")
        self.brand = Brand.objects.create(name="B", slug="b", factory=self.factory)
        self.source = SourceVideo.objects.create(brand=self.brand)
        # popular source.file com um path fake — só usamos para _job_video_path() retornar algo
        # patcheamos _job_video_path nos testes para não exigir arquivo real.

    def _make_job(self, **overrides):
        defaults = dict(
            source_kind="SOURCE",
            source=self.source,
            user=self.user,
            prompt_version="viral",
        )
        defaults.update(overrides)
        return MultipleCreatorJob.objects.create(**defaults)

    @patch("apps.multiple_creator.tasks._transcribe_video")
    @patch("apps.multiple_creator.tasks._job_video_path")
    def test_transcribe_happy_path_source(self, mock_path, mock_transcribe):
        mock_path.return_value = Path("/tmp/fake.mp4")
        with patch.object(Path, "exists", return_value=True):
            mock_transcribe.return_value = _fake_segments(4)
            job = self._make_job()
            multiple_creator_transcribe_task(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, "READY")
        self.assertEqual(len(job.transcript_segments), 4)
        self.assertTrue(job.transcript)
        self.assertEqual(job.progress, 20)
        mock_transcribe.assert_called_once()

    @patch("apps.multiple_creator.tasks._transcribe_video")
    @patch("apps.multiple_creator.tasks._job_video_path")
    def test_transcribe_idempotent_when_not_pending(self, mock_path, mock_transcribe):
        job = self._make_job(status="READY")
        multiple_creator_transcribe_task(job.id)
        mock_transcribe.assert_not_called()
        mock_path.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "READY")

    @patch("apps.multiple_creator.tasks._transcribe_video")
    @patch("apps.multiple_creator.tasks._job_video_path")
    def test_transcribe_empty_segments_sets_error(self, mock_path, mock_transcribe):
        mock_path.return_value = Path("/tmp/fake.mp4")
        with patch.object(Path, "exists", return_value=True):
            mock_transcribe.return_value = []
            job = self._make_job()
            multiple_creator_transcribe_task(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, "ERROR")
        self.assertIn("vazia", job.error.lower())

    @patch("apps.multiple_creator.tasks._transcribe_video")
    @patch("apps.multiple_creator.tasks._job_video_path")
    def test_transcribe_missing_video_sets_error(self, mock_path, mock_transcribe):
        mock_path.return_value = None
        job = self._make_job()
        multiple_creator_transcribe_task(job.id)
        mock_transcribe.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "ERROR")
        self.assertIn("vídeo", job.error.lower())

    @patch("apps.multiple_creator.tasks._download_youtube_to_job")
    @patch("apps.multiple_creator.tasks._transcribe_video")
    @patch("apps.multiple_creator.tasks._job_video_path")
    def test_transcribe_youtube_downloads_first(self, mock_path, mock_transcribe, mock_download):
        mock_download.return_value = Path("/tmp/yt.mp4")
        mock_path.return_value = Path("/tmp/yt.mp4")
        with patch.object(Path, "exists", return_value=True):
            mock_transcribe.return_value = _fake_segments(2)
            job = self._make_job(
                source_kind="YOUTUBE",
                source=None,
                youtube_url="https://www.youtube.com/watch?v=abc",
            )
            multiple_creator_transcribe_task(job.id)
        mock_download.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.status, "READY")


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class MultipleCreatorViewDispatchTests(TestCase):
    """View create dispara a transcribe task via .delay()."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="mc-view", password="x")
        self.client.force_authenticate(user=self.user)
        self.factory = Factory.objects.create(name="F")
        self.brand_a = Brand.objects.create(name="A", slug="a", factory=self.factory)
        self.brand_b = Brand.objects.create(name="B", slug="b", factory=self.factory)

    @patch("apps.multiple_creator.tasks.multiple_creator_transcribe_task.delay")
    def test_create_dispatches_transcribe_task(self, mock_delay):
        payload = {
            "youtube_url": "https://www.youtube.com/watch?v=xyz",
            "brand_ids": [self.brand_a.id, self.brand_b.id],
            "name": "live",
        }
        res = self.client.post("/api/multiple-creator/", payload, format="json")
        self.assertEqual(res.status_code, drf_status.HTTP_201_CREATED, res.data)
        mock_delay.assert_called_once_with(res.data["id"])


class AnalyzeAutoCutsMultiCreatorGateTests(TestCase):
    """Gate _was_transcript_prepopulated_by_multi_creator garante o curto-circuito."""

    def setUp(self):
        self.user = User.objects.create_user(username="gate", password="x")
        self.factory = Factory.objects.create(name="F")
        self.brand = Brand.objects.create(name="B", slug="b", factory=self.factory)

    def _make_analysis(self, **overrides):
        defaults = dict(
            user=self.user,
            brand=self.brand,
            prompt_version="viral",
        )
        defaults.update(overrides)
        return AutoCutAnalysis.objects.create(**defaults)

    def test_returns_false_when_no_transcript_segments(self):
        analysis = self._make_analysis(transcript_segments=None)
        self.assertFalse(_was_transcript_prepopulated_by_multi_creator(analysis))

    def test_returns_false_when_transcript_set_but_no_multi_creator_link(self):
        analysis = self._make_analysis(transcript_segments=_fake_segments(2))
        self.assertFalse(_was_transcript_prepopulated_by_multi_creator(analysis))

    def test_returns_true_when_linked_to_brand_execution_with_transcript(self):
        analysis = self._make_analysis(transcript_segments=_fake_segments(2))
        job = MultipleCreatorJob.objects.create(
            source_kind="YOUTUBE",
            youtube_url="https://x",
            user=self.user,
        )
        MultipleCreatorBrandExecution.objects.create(
            job=job,
            brand=self.brand,
            auto_cut_analysis=analysis,
        )
        self.assertTrue(_was_transcript_prepopulated_by_multi_creator(analysis))
