"""Tests: cleanup_terminal_job_files_task respeita a janela de retencao."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.brands.models import Brand, Factory
from apps.multiple_creator.models import MultipleCreatorJob
from apps.multiple_creator.tasks import cleanup_terminal_job_files_task

User = get_user_model()


class CleanupTerminalJobFilesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mc-cleanup", password="x")
        self.factory = Factory.objects.create(name="F")
        self.brand = Brand.objects.create(name="B", slug="b", factory=self.factory)

    def _make_job(self, *, status, hours_ago, with_file=True):
        job = MultipleCreatorJob.objects.create(
            user=self.user,
            source_kind="FILE",
            status=status,
        )
        if with_file:
            # save=True persiste o atributo file no row.
            job.file.save("fake.mp4", ContentFile(b"x" * 16), save=True)
        # forca updated_at no passado (update direto, sem auto_now)
        MultipleCreatorJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(hours=hours_ago)
        )
        job.refresh_from_db()
        return job

    @override_settings(MULTIPLE_CREATOR_FILE_RETAIN_HOURS=24)
    def test_old_terminal_job_file_is_removed(self):
        old = self._make_job(status="DONE", hours_ago=48)
        self.assertTrue(old.file)
        result = cleanup_terminal_job_files_task()
        old.refresh_from_db()
        self.assertFalse(old.file)
        self.assertEqual(result["removed"], 1)

    @override_settings(MULTIPLE_CREATOR_FILE_RETAIN_HOURS=24)
    def test_recent_terminal_job_file_is_kept(self):
        recent = self._make_job(status="DONE", hours_ago=2)
        self.assertTrue(recent.file)
        result = cleanup_terminal_job_files_task()
        recent.refresh_from_db()
        self.assertTrue(recent.file)
        self.assertEqual(result["removed"], 0)

    @override_settings(MULTIPLE_CREATOR_FILE_RETAIN_HOURS=24)
    def test_running_job_file_is_kept_regardless_of_age(self):
        running = self._make_job(status="RUNNING_BRANDS", hours_ago=72)
        result = cleanup_terminal_job_files_task()
        running.refresh_from_db()
        self.assertTrue(running.file)
        self.assertEqual(result["removed"], 0)

    @override_settings(MULTIPLE_CREATOR_FILE_RETAIN_HOURS=24)
    def test_partial_and_error_old_jobs_also_cleaned(self):
        p = self._make_job(status="PARTIAL", hours_ago=30)
        e = self._make_job(status="ERROR", hours_ago=30)
        result = cleanup_terminal_job_files_task()
        p.refresh_from_db()
        e.refresh_from_db()
        self.assertFalse(p.file)
        self.assertFalse(e.file)
        self.assertEqual(result["removed"], 2)

    @override_settings(MULTIPLE_CREATOR_FILE_RETAIN_HOURS=24)
    def test_terminal_job_without_file_is_noop(self):
        bare = self._make_job(status="DONE", hours_ago=48, with_file=False)
        result = cleanup_terminal_job_files_task()
        bare.refresh_from_db()
        self.assertEqual(result["removed"], 0)

    @override_settings(MULTIPLE_CREATOR_FILE_RETAIN_HOURS=1)
    def test_retention_window_is_configurable(self):
        # Job de 2h atras: com retain=1 deve ser removido.
        job = self._make_job(status="DONE", hours_ago=2)
        result = cleanup_terminal_job_files_task()
        job.refresh_from_db()
        self.assertFalse(job.file)
        self.assertEqual(result["removed"], 1)
