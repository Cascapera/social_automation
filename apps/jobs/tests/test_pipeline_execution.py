from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.jobs.models import Job, PipelineExecution, RenderOutput, StageExecution
from apps.jobs.services.pipeline_execution import (
    JOB_AGGREGATE_TYPE,
    JOB_PIPELINE_TYPE,
    STAGE_JOB_PROCESSING,
    STAGE_SUBTITLE_BURN,
    STAGE_TRANSCRIPTION,
    get_or_create_job_pipeline_execution,
)
from apps.jobs.tasks import burn_subtitles_task, generate_subtitles_task, process_job


class PipelineExecutionTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _create_job(self, **overrides) -> Job:
        defaults = {"name": "Pipeline Job"}
        defaults.update(overrides)
        return Job.objects.create(**defaults)

    def _create_output(self, job: Job) -> RenderOutput:
        return RenderOutput.objects.create(
            job=job,
            file=SimpleUploadedFile(
                "video.mp4",
                b"fake-video",
                content_type="video/mp4",
            ),
        )

    def _mark_job_done(self, job_id: int) -> None:
        Job.objects.filter(pk=job_id).update(status="DONE")

    def test_pipeline_execution_is_created_for_job_flow(self):
        job = self._create_job()

        with patch("apps.jobs.tasks.run_job", side_effect=self._mark_job_done):
            process_job.run(job.id)

        job.refresh_from_db()
        pipeline_execution = PipelineExecution.objects.get(
            pipeline_type=JOB_PIPELINE_TYPE,
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
        )
        stage_execution = StageExecution.objects.get(
            pipeline_execution=pipeline_execution,
            stage_name=STAGE_JOB_PROCESSING,
        )

        self.assertEqual(pipeline_execution.correlation_id, job.correlation_id)
        self.assertEqual(stage_execution.input_payload, {"job_id": job.id})
        self.assertTrue(stage_execution.queue_name)
        self.assertTrue(stage_execution.task_name)

    def test_stage_execution_is_completed_on_success(self):
        job = self._create_job(status="DONE")
        self._create_output(job)
        segments = [{"start": 0.0, "end": 1.0, "text": "teste"}]

        with patch("apps.jobs.tasks.generate_subtitles", return_value=segments):
            generate_subtitles_task.run(job.id)

        pipeline_execution = PipelineExecution.objects.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
        )
        stage_execution = StageExecution.objects.get(
            pipeline_execution=pipeline_execution,
            stage_name=STAGE_TRANSCRIPTION,
        )
        job.refresh_from_db()

        self.assertEqual(job.subtitle_status, "ready_for_edit")
        self.assertEqual(stage_execution.status, StageExecution.Status.COMPLETED)
        self.assertEqual(stage_execution.output_payload["segments_count"], 1)
        self.assertEqual(pipeline_execution.status, PipelineExecution.Status.RUNNING)
        self.assertEqual(pipeline_execution.current_stage, STAGE_TRANSCRIPTION)

    def test_stage_execution_is_marked_failed_on_exception(self):
        job = self._create_job(status="DONE")
        self._create_output(job)

        with patch(
            "apps.jobs.tasks.generate_subtitles",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                generate_subtitles_task.run(job.id)

        pipeline_execution = PipelineExecution.objects.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
        )
        stage_execution = StageExecution.objects.get(
            pipeline_execution=pipeline_execution,
            stage_name=STAGE_TRANSCRIPTION,
        )

        self.assertEqual(stage_execution.status, StageExecution.Status.FAILED)
        self.assertEqual(stage_execution.error_class, "RuntimeError")
        self.assertEqual(stage_execution.error_message, "boom")
        self.assertEqual(pipeline_execution.status, PipelineExecution.Status.FAILED)
        self.assertEqual(pipeline_execution.failure_reason, "boom")

    def test_repeated_get_or_create_reuses_same_pipeline_execution(self):
        job = self._create_job(correlation_id="cid-123")

        first, first_created = get_or_create_job_pipeline_execution(job)
        second, second_created = get_or_create_job_pipeline_execution(job)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PipelineExecution.objects.count(), 1)

    def test_repeated_stage_execution_reuses_same_row(self):
        job = self._create_job(status="DONE")
        self._create_output(job)
        segments = [{"start": 0.0, "end": 1.0, "text": "teste"}]

        with patch("apps.jobs.tasks.generate_subtitles", return_value=segments):
            generate_subtitles_task.run(job.id)
            generate_subtitles_task.run(job.id)

        pipeline_execution = PipelineExecution.objects.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
        )
        stage_qs = StageExecution.objects.filter(
            pipeline_execution=pipeline_execution,
            stage_name=STAGE_TRANSCRIPTION,
        )
        stage_execution = stage_qs.get()

        self.assertEqual(stage_qs.count(), 1)
        self.assertEqual(stage_execution.retry_count, 1)
        self.assertEqual(stage_execution.status, StageExecution.Status.COMPLETED)

    def test_pipeline_is_completed_after_final_integrated_stage(self):
        job = self._create_job(
            status="DONE",
            subtitle_segments=[{"start": 0.0, "end": 1.0, "text": "teste"}],
            subtitle_style={"animated": False},
        )
        self._create_output(job)

        def fake_burn_subtitles(video_path, subs_path, output_tmp, style, segments):
            self.assertTrue(Path(video_path).exists())
            self.assertTrue(Path(subs_path).exists())
            output_tmp.write_bytes(b"burned-video")

        with (
            patch("apps.jobs.tasks.has_nvenc", return_value=False),
            patch("apps.jobs.tasks.segments_to_srt", return_value="1\n00:00:00,000 --> 00:00:01,000\nteste\n"),
            patch("apps.jobs.tasks.burn_subtitles", side_effect=fake_burn_subtitles),
        ):
            burn_subtitles_task.run(job.id)

        pipeline_execution = PipelineExecution.objects.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
        )
        stage_execution = StageExecution.objects.get(
            pipeline_execution=pipeline_execution,
            stage_name=STAGE_SUBTITLE_BURN,
        )
        job.refresh_from_db()

        self.assertEqual(job.subtitle_status, "burned")
        self.assertEqual(stage_execution.status, StageExecution.Status.COMPLETED)
        self.assertEqual(pipeline_execution.status, PipelineExecution.Status.COMPLETED)
        self.assertEqual(pipeline_execution.current_stage, STAGE_SUBTITLE_BURN)
        self.assertIsNotNone(pipeline_execution.completed_at)
