from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.brands.models import Brand, BrandSocialAccount, Factory
from apps.jobs.models import IdempotencyKey, Job, RenderOutput, ScheduledPost
from apps.social.services.idempotency import (
    acquire_idempotency_key,
    get_existing_idempotency_result,
    mark_idempotency_success,
)
from apps.social.tasks import _run_post_to_platforms

User = get_user_model()


class PublishIdempotencyTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="Factory Idempotency")
        self.brand = Brand.objects.create(
            name="Brand Idempotency",
            slug="brand-idempotency",
            factory=self.factory,
            upload_post_tiktok_enabled=False,
            upload_post_x_enabled=False,
            upload_post_instagram_enabled=False,
            upload_post_youtube_enabled=False,
        )
        self.user = User.objects.create_user(username="publish-idemp", password="securepass1")
        self.account = BrandSocialAccount.objects.create(
            brand=self.brand,
            platform="YT",
            channel_id="channel-yt-1",
            account_name="Canal Teste",
        )
        self.job = Job.objects.create(user=self.user, brand=self.brand, name="Job Publish")
        RenderOutput.objects.create(
            job=self.job,
            file=SimpleUploadedFile("video.mp4", b"video-idempotency-data", content_type="video/mp4"),
        )

    def _create_post(self, *, title: str) -> ScheduledPost:
        return ScheduledPost.objects.create(
            job=self.job,
            social_account=self.account,
            platforms=["YT"],
            scheduled_at=timezone.now() + timedelta(minutes=1),
            title=title,
            status="PENDING",
        )

    def test_same_publish_key_called_twice_reuses_stored_result(self):
        first_post = self._create_post(title="Primeira tentativa")
        second_post = self._create_post(title="Segunda tentativa")
        publisher = Mock()
        publisher.publish = Mock(return_value={"video_id": "yt-video-123"})

        with patch("apps.social.publishers.get_publisher", return_value=publisher):
            first_result = _run_post_to_platforms(first_post.id)
            second_result = _run_post_to_platforms(second_post.id)

        first_post.refresh_from_db()
        second_post.refresh_from_db()

        self.assertEqual(first_result["status"], "DONE")
        self.assertEqual(second_result["status"], "DONE")
        self.assertEqual(first_post.external_ids["YT"], "yt-video-123")
        self.assertEqual(second_post.external_ids["YT"], "yt-video-123")
        self.assertEqual(publisher.publish.call_count, 1)
        self.assertEqual(IdempotencyKey.objects.count(), 1)

    def test_success_persists_result_payload(self):
        post = self._create_post(title="Persistir payload")
        publisher = Mock()
        publisher.publish = Mock(return_value={"video_id": "yt-video-payload"})

        with patch("apps.social.publishers.get_publisher", return_value=publisher):
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        key = IdempotencyKey.objects.get()
        stored_payload = get_existing_idempotency_result(key.key)

        self.assertEqual(result["status"], "DONE")
        self.assertEqual(key.status, IdempotencyKey.Status.SUCCEEDED)
        self.assertEqual(key.result_payload["external_ids"]["YT"], "yt-video-payload")
        self.assertEqual(stored_payload["external_ids"]["YT"], "yt-video-payload")
        self.assertEqual(post.external_ids["YT"], "yt-video-payload")

    def test_failure_marks_idempotency_key_failed(self):
        post = self._create_post(title="Falha publisher")
        publisher = Mock()
        publisher.publish = Mock(side_effect=RuntimeError("boom publish"))

        with patch("apps.social.publishers.get_publisher", return_value=publisher):
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        key = IdempotencyKey.objects.get()

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(post.status, "FAILED")
        self.assertEqual(key.status, IdempotencyKey.Status.FAILED)
        self.assertIn("boom publish", key.error_message)

    def test_repeated_acquire_keeps_single_row_and_blocks_execution(self):
        first = acquire_idempotency_key(
            key="publish:YT:channel-yt-1:fingerprint-1",
            operation_name="publish",
            aggregate_type="ScheduledPost",
            aggregate_id=1,
        )
        second = acquire_idempotency_key(
            key="publish:YT:channel-yt-1:fingerprint-1",
            operation_name="publish",
            aggregate_type="ScheduledPost",
            aggregate_id=2,
        )

        self.assertEqual(first.outcome, "acquired")
        self.assertEqual(second.outcome, "in_progress")
        self.assertEqual(IdempotencyKey.objects.count(), 1)

        mark_idempotency_success(
            key="publish:YT:channel-yt-1:fingerprint-1",
            result_payload={"external_ids": {"YT": "yt-existing"}},
        )
        third = acquire_idempotency_key(
            key="publish:YT:channel-yt-1:fingerprint-1",
            operation_name="publish",
            aggregate_type="ScheduledPost",
            aggregate_id=3,
        )

        self.assertEqual(third.outcome, "succeeded")
