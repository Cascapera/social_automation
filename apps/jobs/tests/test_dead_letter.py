from __future__ import annotations

import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from apps.jobs.admin import DeadLetterJobAdmin
from apps.jobs.models import DeadLetterJob, Job, PipelineExecution, RenderOutput, StageExecution
from apps.jobs.services.dead_letter import replay_dead_letter_job
from apps.jobs.services.pipeline_execution import (
    JOB_AGGREGATE_TYPE,
    STAGE_JOB_PROCESSING,
    STAGE_TRANSCRIPTION,
)
from apps.jobs.tasks import generate_subtitles_task, process_job

User = get_user_model()


class DeadLetterJobTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.request_factory = RequestFactory()
        self.admin_site = AdminSite()
        self.admin_user = User.objects.create_superuser(
            username="deadletter-admin",
            email="deadletter-admin@example.com",
            password="securepass1",
        )

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _create_job(self, **overrides) -> Job:
        defaults = {"name": "DLQ Job"}
        defaults.update(overrides)
        return Job.objects.create(**defaults)

    def _create_dead_letter(self, **overrides) -> DeadLetterJob:
        defaults = {
            "aggregate_type": JOB_AGGREGATE_TYPE,
            "aggregate_id": 1,
            "job_name": "process_job",
            "error_category": DeadLetterJob.ErrorCategory.UNKNOWN,
            "status": DeadLetterJob.Status.OPEN,
        }
        defaults.update(overrides)
        return DeadLetterJob.objects.create(**defaults)

    def _create_output(self, job: Job) -> RenderOutput:
        return RenderOutput.objects.create(
            job=job,
            file=SimpleUploadedFile(
                "video.mp4",
                b"fake-video",
                content_type="video/mp4",
            ),
        )

    def test_failure_creates_dead_letter_job(self):
        job = self._create_job()

        with self.assertRaises(ValueError):
            process_job.run(job.id)

        pipeline_execution = PipelineExecution.objects.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
        )
        stage_execution = StageExecution.objects.get(
            pipeline_execution=pipeline_execution,
            stage_name=STAGE_JOB_PROCESSING,
        )
        dead_letter = DeadLetterJob.objects.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
            job_name="process_job",
        )

        self.assertEqual(dead_letter.pipeline_execution_id, pipeline_execution.id)
        self.assertEqual(dead_letter.stage_execution_id, stage_execution.id)
        self.assertEqual(dead_letter.error_class, "ValueError")
        self.assertIn("pelo menos 1 corte", dead_letter.error_message)
        self.assertEqual(dead_letter.status, DeadLetterJob.Status.OPEN)
        self.assertEqual(dead_letter.payload_json["stage_name"], STAGE_JOB_PROCESSING)

    def test_early_validation_failure_creates_dead_letter_job(self):
        job = self._create_job(status="DONE")
        output = self._create_output(job)
        output.file.delete(save=True)

        generate_subtitles_task.run(job.id)

        dead_letter = DeadLetterJob.objects.get(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
            job_name="generate_subtitles_task",
        )
        self.assertEqual(dead_letter.error_class, "StageValidationError")
        self.assertEqual(
            dead_letter.error_category,
            DeadLetterJob.ErrorCategory.NON_RETRYABLE,
        )
        self.assertEqual(dead_letter.status, DeadLetterJob.Status.OPEN)
        self.assertEqual(dead_letter.payload_json["stage_name"], STAGE_TRANSCRIPTION)

    def test_admin_replay_calls_correct_task(self):
        job = self._create_job()
        dead_letter = self._create_dead_letter(
            aggregate_id=job.id,
            job_name="generate_subtitles_task",
        )
        admin_instance = DeadLetterJobAdmin(DeadLetterJob, self.admin_site)
        request = self.request_factory.post("/admin/jobs/deadletterjob/")
        request.user = self.admin_user

        with (
            patch.object(admin_instance, "message_user"),
            patch(
                "apps.jobs.tasks.generate_subtitles_task.delay",
                return_value=SimpleNamespace(id="replay-task-1"),
            ) as delay_mock,
        ):
            admin_instance.replay_selected_dead_letters(
                request,
                DeadLetterJob.objects.filter(pk=dead_letter.pk),
            )

        delay_mock.assert_called_once_with(job.id)

    def test_replay_updates_replayed_fields_and_status(self):
        job = self._create_job()
        dead_letter = self._create_dead_letter(
            aggregate_id=job.id,
            job_name="process_job",
        )

        with patch(
            "apps.jobs.tasks.process_job.delay",
            return_value=SimpleNamespace(id="replay-task-2"),
        ):
            replay_dead_letter_job(dead_letter, user=self.admin_user)

        dead_letter.refresh_from_db()
        self.assertEqual(dead_letter.status, DeadLetterJob.Status.REPLAYED)
        self.assertEqual(dead_letter.replayed_by_id, self.admin_user.id)
        self.assertIsNotNone(dead_letter.replayed_at)
        self.assertEqual(dead_letter.replay_result_json["status"], "dispatched")
        self.assertEqual(dead_letter.replay_result_json["task_id"], "replay-task-2")

    def test_replay_rejects_unsupported_job_and_keeps_open(self):
        dead_letter = self._create_dead_letter(job_name="unknown_task")

        with self.assertRaises(ValueError):
            replay_dead_letter_job(dead_letter, user=self.admin_user)

        dead_letter.refresh_from_db()
        self.assertEqual(dead_letter.status, DeadLetterJob.Status.OPEN)
        self.assertEqual(dead_letter.error_class, "ValueError")
        self.assertIn("Unsupported dead letter job", dead_letter.error_message)
        self.assertEqual(dead_letter.replay_result_json["status"], "dispatch_failed")

    def test_duplicate_failure_does_not_create_uncontrolled_duplicates(self):
        job = self._create_job(status="DONE")
        output = self._create_output(job)
        output.file.delete(save=True)

        generate_subtitles_task.run(job.id)
        generate_subtitles_task.run(job.id)

        dead_letters = DeadLetterJob.objects.filter(
            aggregate_type=JOB_AGGREGATE_TYPE,
            aggregate_id=job.id,
            job_name="generate_subtitles_task",
            status=DeadLetterJob.Status.OPEN,
        )
        dead_letter = dead_letters.get()

        self.assertEqual(dead_letters.count(), 1)
        self.assertEqual(dead_letter.retry_count, 1)
        self.assertEqual(dead_letter.error_class, "StageValidationError")

    def test_admin_is_read_only(self):
        dead_letter = self._create_dead_letter()
        admin_instance = DeadLetterJobAdmin(DeadLetterJob, self.admin_site)
        request = self.request_factory.get(f"/admin/jobs/deadletterjob/{dead_letter.id}/change/")
        request.user = self.admin_user

        self.assertFalse(admin_instance.has_add_permission(request))
        self.assertFalse(admin_instance.has_delete_permission(request, dead_letter))
        self.assertIn("status", admin_instance.get_readonly_fields(request, dead_letter))
        self.assertIn("replay_result_json", admin_instance.get_readonly_fields(request, dead_letter))

        with patch.object(
            admin.ModelAdmin,
            "render_change_form",
            side_effect=lambda *args, **kwargs: args[1] if len(args) > 1 else kwargs["context"],
        ):
            context = admin_instance.render_change_form(
                request,
                {},
                add=False,
                change=True,
                form_url="",
                obj=dead_letter,
            )

        self.assertFalse(context["show_save"])
        self.assertFalse(context["show_save_and_continue"])
        self.assertFalse(context["show_save_and_add_another"])
        self.assertFalse(context["show_save_as_new"])
        self.assertFalse(context["show_delete"])
