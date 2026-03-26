"""Job action tests (archive, delete, pending posts)."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.brands.models import Brand, Factory
from apps.jobs.models import Job, RenderOutput, ScheduledPost
from apps.jobs.services.job_actions import (
    archive_job,
    delete_job,
    delete_job_output,
    has_pending_scheduled_posts,
)

User = get_user_model()


class JobActionsTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="FA")
        self.brand = Brand.objects.create(name="BA", slug="ba", factory=self.factory)
        self.user = User.objects.create_user(username="ja", password="securepass1")

    def test_has_pending_scheduled_posts_false_when_empty(self):
        job = Job.objects.create(user=self.user, brand=self.brand, name="J1")
        self.assertFalse(has_pending_scheduled_posts(job))

    def test_has_pending_scheduled_posts_true(self):
        job = Job.objects.create(user=self.user, brand=self.brand, name="J2")
        ScheduledPost.objects.create(
            job=job,
            platforms=["YT"],
            scheduled_at=timezone.now() + timedelta(days=1),
            title="t",
            status="PENDING",
        )
        self.assertTrue(has_pending_scheduled_posts(job))

    def test_archive_job_removes_output_and_sets_archived(self):
        job = Job.objects.create(user=self.user, brand=self.brand, name="J3")
        RenderOutput.objects.create(
            job=job,
            file=SimpleUploadedFile("x.mp4", b"fake", content_type="video/mp4"),
        )
        archive_job(job)
        job.refresh_from_db()
        self.assertTrue(job.archived)
        self.assertFalse(RenderOutput.objects.filter(job=job).exists())

    def test_delete_job_removes_job(self):
        job = Job.objects.create(user=self.user, brand=self.brand, name="J4")
        jid = job.id
        delete_job(job)
        self.assertFalse(Job.objects.filter(id=jid).exists())

    def test_delete_job_output_no_output(self):
        job = Job.objects.create(user=self.user, brand=self.brand, name="J5")
        self.assertFalse(delete_job_output(job))
