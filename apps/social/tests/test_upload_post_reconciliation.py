from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.brands.models import Brand, BrandSocialAccount, Factory
from apps.jobs.models import Job, RenderOutput, ScheduledPost
from apps.social.publishers.upload_post import UploadPostErrorKind, UploadPostPublishError
from apps.social.services.upload_post_reconciliation import (
    ReconcileDecision,
    reconcile_upload_post_status,
)
from apps.social.tasks import _run_post_to_platforms

User = get_user_model()


class UploadPostReconciliationTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="Factory UP Rec")
        self.brand = Brand.objects.create(
            name="Brand UP Rec",
            slug="brand-up-rec",
            factory=self.factory,
            upload_post_tiktok_enabled=False,
            upload_post_x_enabled=False,
            upload_post_instagram_enabled=False,
            upload_post_youtube_enabled=True,
        )
        self.user = User.objects.create_user(username="up-rec-user", password="securepass1")
        self.account = BrandSocialAccount.objects.create(
            brand=self.brand,
            platform="YTB",
            channel_id="channel-yt-1",
            account_name="Canal Teste",
        )
        self.job = Job.objects.create(user=self.user, brand=self.brand, name="Job UP")
        RenderOutput.objects.create(
            job=self.job,
            file=SimpleUploadedFile("video.mp4", b"x" * 2048, content_type="video/mp4"),
        )

    def _post_ytb(self, **kwargs) -> ScheduledPost:
        defaults = dict(
            job=self.job,
            social_account=self.account,
            platforms=["YTB"],
            scheduled_at=timezone.now() - timedelta(seconds=1),
            title="Título",
            status="PENDING",
        )
        defaults.update(kwargs)
        return ScheduledPost.objects.create(**defaults)

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_http_504_does_not_call_native_youtube_same_run(self, mock_up: MagicMock):
        mock_up.side_effect = UploadPostPublishError(
            "504",
            status_code=504,
            retriable=False,
            kind=UploadPostErrorKind.UNKNOWN_PENDING_CONFIRMATION,
            request_id="req-abc",
        )
        post = self._post_ytb()
        native = MagicMock()
        native.publish = MagicMock(return_value={"video_id": "native-should-not-run"})

        with patch("apps.social.publishers.get_publisher", return_value=native):
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(result.get("skipped"), "upload_post_unknown_awaiting_reconciliation")
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(post.external_ids.get("upload_post_reconciliation_state"), "pending")
        self.assertEqual(post.external_ids.get("upload_post_request_id"), "req-abc")
        native.publish.assert_not_called()

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_unknown_persists_provider_fields_when_partial(self, mock_up: MagicMock):
        mock_up.side_effect = UploadPostPublishError(
            "499",
            status_code=499,
            retriable=False,
            kind=UploadPostErrorKind.UNKNOWN_PENDING_CONFIRMATION,
            request_id="rid-1",
            job_id="jid-1",
        )
        post = self._post_ytb()

        with patch("apps.social.publishers.get_publisher"):
            _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(post.external_ids.get("upload_post_request_id"), "rid-1")
        self.assertEqual(post.external_ids.get("upload_post_job_id"), "jid-1")
        self.assertIn("upload_post_last_checked_at", post.external_ids)

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_reconcile_completed_marks_success(self, mock_status: MagicMock):
        mock_status.return_value = (
            {
                "status": "completed",
                "results": [
                    {
                        "platform": "youtube",
                        "success": True,
                        "message": "https://youtu.be/dQw4w9WgXcQ",
                    }
                ],
            },
            None,
        )
        ext = {
            "upload_post_request_id": "r1",
            "upload_post_reconciliation_state": "pending",
        }
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.CONFIRMED_SUCCESS)
        self.assertEqual(out.youtube_video_id, "dQw4w9WgXcQ")

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_reconcile_failed_allows_fallback_flag(self, mock_status: MagicMock):
        mock_status.return_value = (
            {
                "status": "completed",
                "results": [
                    {
                        "platform": "youtube",
                        "success": False,
                        "message": "rejected",
                    }
                ],
            },
            None,
        )
        ext = {"upload_post_request_id": "r1", "upload_post_reconciliation_state": "pending"}
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.CONFIRMED_FAILURE)

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_reconcile_pending_waits(self, mock_status: MagicMock):
        mock_status.return_value = ({"status": "in_progress", "results": []}, None)
        ext = {"upload_post_request_id": "r1", "upload_post_reconciliation_state": "pending"}
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.WAIT)

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_reconcile_queued_treated_as_pending_wait(self, mock_status: MagicMock):
        mock_status.return_value = ({"status": "queued", "results": []}, None)
        ext = {"upload_post_request_id": "r1", "upload_post_reconciliation_state": "pending"}
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.WAIT)

    def test_native_invalid_grant_marks_external_ids_and_skips_publish(self):
        from apps.social.publishers.youtube import YouTubePublishError

        self.brand.upload_post_youtube_enabled = False
        self.brand.save(update_fields=["upload_post_youtube_enabled"])

        post = self._post_ytb()
        publisher = MagicMock()
        publisher.publish = MagicMock(
            side_effect=YouTubePublishError(
                "invalid_grant",
                reason="invalidGrant",
                retriable=False,
            )
        )

        with patch("apps.social.publishers.get_publisher", return_value=publisher):
            _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertTrue(post.external_ids.get("youtube_native_invalid_grant"))
        self.assertIn("invalid_grant", (post.error or "").lower())
