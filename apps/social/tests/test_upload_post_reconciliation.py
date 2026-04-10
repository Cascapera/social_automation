from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.brands.models import Brand, BrandSocialAccount, Factory
from apps.jobs.models import Job, RenderOutput, ScheduledPost
from apps.social.publishers.upload_post import (
    UploadPostErrorKind,
    UploadPostPublishError,
    publish_to_upload_post,
)
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

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_unknown_without_provider_ids_stores_client_request_id(self, mock_up: MagicMock):
        post = self._post_ytb(title="Primeiro")
        mock_up.side_effect = UploadPostPublishError(
            "timeout sem ids",
            status_code=504,
            retriable=False,
            kind=UploadPostErrorKind.UNKNOWN_PENDING_CONFIRMATION,
        )

        with patch("apps.social.publishers.get_publisher") as mock_native:
            first_result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(first_result.get("skipped"), "upload_post_unknown_awaiting_reconciliation")
        self.assertTrue(str(post.external_ids.get("upload_post_request_id") or "").startswith("upreq-"))
        self.assertNotIn("upload_post_no_provider_id_check_count", post.external_ids)
        mock_up.assert_called_once()
        mock_native.assert_not_called()

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_upload_post_passes_client_request_and_idempotency_keys(self, mock_up: MagicMock):
        mock_up.return_value = {"success": True, "data": {}}
        post = self._post_ytb(title="Com chaves")

        with patch("apps.social.publishers.get_publisher") as mock_native:
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        call_kwargs = mock_up.call_args.kwargs
        self.assertEqual(result.get("status"), "DONE")
        self.assertTrue(call_kwargs["request_id"].startswith("upreq-"))
        self.assertTrue(call_kwargs["idempotency_key"].startswith("upidem-"))
        self.assertEqual(post.external_ids.get("upload_post_request_id"), call_kwargs["request_id"])
        mock_native.assert_not_called()

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_pending_reconciliation_without_provider_ids_does_not_send_normally(self, mock_up: MagicMock):
        post = self._post_ytb(
            external_ids={
                "upload_post_reconciliation_state": "pending",
            }
        )

        result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(result.get("skipped"), "upload_post_no_provider_id")
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(post.external_ids.get("upload_post_no_provider_id_check_count"), 1)
        mock_up.assert_not_called()

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_pending_reconciliation_with_provider_ids_triggers_reconcile_first(
        self,
        mock_status: MagicMock,
        mock_up: MagicMock,
    ):
        mock_status.return_value = (
            {"status": "in_progress", "results": []},
            None,
        )
        post = self._post_ytb(
            external_ids={
                "upload_post_reconciliation_state": "pending",
                "upload_post_request_id": "req-xyz",
            }
        )

        result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(result.get("skipped"), "upload_post_reconciliation_wait")
        self.assertEqual(post.status, "PENDING")
        mock_status.assert_called_once()
        mock_up.assert_not_called()

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_provider_not_found_starts_controlled_unknown_path(
        self,
        mock_status: MagicMock,
        mock_up: MagicMock,
    ):
        mock_status.return_value = (
            {
                "status": "not_found",
                "message": "No upload request found with this ID",
            },
            None,
        )
        post = self._post_ytb(
            external_ids={
                "upload_post_reconciliation_state": "pending",
                "upload_post_request_id": "req-missing",
            }
        )

        result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(result.get("skipped"), "upload_post_provider_not_found")
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(post.external_ids.get("upload_post_last_status"), "not_found")
        self.assertEqual(post.external_ids.get("upload_post_no_provider_id_check_count"), 1)
        mock_status.assert_called_once()
        mock_up.assert_not_called()

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_second_no_provider_id_check_starts_controlled_resend(self, mock_up: MagicMock):
        mock_up.return_value = {
            "success": True,
            "request_id": "retry-request-id",
            "job_id": "retry-job-id",
            "data": {},
        }
        post = self._post_ytb(
            external_ids={
                "upload_post_reconciliation_state": "pending",
                "upload_post_no_provider_id_check_count": 1,
            }
        )

        with patch("apps.social.publishers.get_publisher") as mock_native:
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(result.get("status"), "DONE")
        self.assertEqual(post.external_ids.get("upload_post_request_id"), "retry-request-id")
        self.assertEqual(post.external_ids.get("upload_post_job_id"), "retry-job-id")
        self.assertNotIn("upload_post_reconciliation_state", post.external_ids)
        self.assertNotIn("upload_post_resend_count", post.external_ids)
        mock_up.assert_called_once()
        mock_native.assert_not_called()

    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_no_provider_id_after_controlled_resend_falls_back_to_native_youtube(self, mock_up: MagicMock):
        post = self._post_ytb(
            external_ids={
                "upload_post_reconciliation_state": "pending",
                "upload_post_resend_count": 1,
            }
        )
        native = MagicMock()
        native.publish = MagicMock(return_value={"video_id": "native-video-id"})

        with patch("apps.social.publishers.get_publisher", return_value=native):
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(result.get("status"), "DONE")
        self.assertEqual(post.status, "DONE")
        self.assertEqual(post.external_ids.get("YTB"), "native-video-id")
        self.assertNotIn("upload_post_reconciliation_state", post.external_ids)
        self.assertNotIn("upload_post_youtube_terminal_failure", post.external_ids)
        self.assertNotIn("upload_post_skip_after_unknown_no_id", post.external_ids)
        mock_up.assert_not_called()
        native.publish.assert_called_once()

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_provider_not_found_after_controlled_resend_falls_back_to_native_youtube(
        self,
        mock_up: MagicMock,
        mock_status: MagicMock,
    ):
        mock_status.return_value = (
            {
                "status": "not_found",
                "message": "No upload request found with this ID",
            },
            None,
        )
        post = self._post_ytb(
            external_ids={
                "upload_post_reconciliation_state": "pending",
                "upload_post_request_id": "req-missing",
                "upload_post_resend_count": 1,
            }
        )
        native = MagicMock()
        native.publish = MagicMock(return_value={"video_id": "native-video-id"})

        with patch("apps.social.publishers.get_publisher", return_value=native):
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(result.get("status"), "DONE")
        self.assertEqual(post.status, "DONE")
        self.assertEqual(post.external_ids.get("YTB"), "native-video-id")
        self.assertEqual(post.external_ids.get("upload_post_last_status"), "provider_not_found_fallback_native")
        self.assertNotIn("upload_post_reconciliation_state", post.external_ids)
        self.assertNotIn("upload_post_youtube_terminal_failure", post.external_ids)
        self.assertNotIn("upload_post_skip_after_unknown_no_id", post.external_ids)
        mock_status.assert_called_once()
        mock_up.assert_not_called()
        native.publish.assert_called_once()

    @patch("apps.social.tasks._native_youtube_fallback_available", return_value=False)
    @patch("apps.social.publishers.upload_post.publish_to_upload_post")
    def test_no_provider_id_after_controlled_resend_fails_without_native_fallback(
        self,
        mock_up: MagicMock,
        _mock_native_available: MagicMock,
    ):
        post = self._post_ytb(
            external_ids={
                "upload_post_reconciliation_state": "pending",
                "upload_post_resend_count": 1,
            }
        )

        with patch("apps.social.publishers.get_publisher") as mock_native:
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(result.get("status"), "FAILED")
        self.assertEqual(post.status, "FAILED")
        self.assertIn("loop infinito", post.error)
        mock_up.assert_not_called()
        mock_native.assert_not_called()

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
    def test_reconcile_processing_waits(self, mock_status: MagicMock):
        mock_status.return_value = ({"status": "processing", "results": []}, None)
        ext = {"upload_post_request_id": "r1", "upload_post_reconciliation_state": "pending"}
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.WAIT)

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_reconcile_queued_treated_as_pending_wait(self, mock_status: MagicMock):
        mock_status.return_value = ({"status": "queued", "results": []}, None)
        ext = {"upload_post_request_id": "r1", "upload_post_reconciliation_state": "pending"}
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.WAIT)

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_reconcile_retryable_result_waits(self, mock_status: MagicMock):
        mock_status.return_value = (
            {
                "status": "completed",
                "results": [
                    {
                        "platform": "youtube",
                        "status": "retryable",
                        "success": False,
                        "message": "automatic retry queued",
                    }
                ],
            },
            None,
        )
        ext = {"upload_post_request_id": "r1", "upload_post_reconciliation_state": "pending"}
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.WAIT)

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_reconcile_top_level_failed_allows_fallback(self, mock_status: MagicMock):
        mock_status.return_value = (
            {
                "status": "failed",
                "results": [
                    {
                        "platform": "youtube",
                        "status": "failed",
                        "success": False,
                        "error": "provider rejected upload",
                    }
                ],
            },
            None,
        )
        ext = {"upload_post_request_id": "r1", "upload_post_reconciliation_state": "pending"}
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.CONFIRMED_FAILURE)
        self.assertIn("provider rejected upload", out.detail)

    @patch("apps.social.services.upload_post_reconciliation.fetch_upload_post_status")
    def test_reconcile_not_found_uses_provider_not_found_path(self, mock_status: MagicMock):
        mock_status.return_value = (
            {
                "status": "not_found",
                "message": "No upload request found with this ID",
            },
            None,
        )
        ext = {"upload_post_request_id": "r1", "upload_post_reconciliation_state": "pending"}
        out = reconcile_upload_post_status(external_ids=ext, needs_youtube=True)
        self.assertEqual(out.decision, ReconcileDecision.PROVIDER_NOT_FOUND)
        self.assertIn("No upload request found", out.detail)

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


class UploadPostPublisherTitleTests(TestCase):
    @override_settings(UPLOAD_POST_API_KEY="test-key")
    @patch("apps.social.publishers.upload_post.requests.post")
    def test_upload_post_sanitizes_title_like_youtube(self, mock_post: MagicMock):
        class _Resp:
            status_code = 200
            content = b"{}"

            def json(self):
                return {"request_id": "rid-1"}

        mock_post.return_value = _Resp()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"video-bytes")
            tmp_path = tmp.name
        try:
            publish_to_upload_post(
                video_path=tmp_path,
                brand_id=1,
                platforms=["YOUTUBE"],
                title="  T\x01itulo <muito>    grande  ",
                description_by_platform={"YOUTUBE": "desc"},
            )
        finally:
            os.unlink(tmp_path)

        data = mock_post.call_args.kwargs["data"]
        title_field = next(value for key, value in data if key == "title")
        self.assertLessEqual(len(title_field), 100)
        self.assertNotIn("\x01", title_field)
        self.assertNotIn("<", title_field)
        self.assertNotIn(">", title_field)

    @override_settings(UPLOAD_POST_API_KEY="test-key")
    @patch("apps.social.publishers.upload_post.requests.post")
    def test_upload_post_sends_client_request_and_idempotency_identifiers(self, mock_post: MagicMock):
        class _Resp:
            status_code = 202
            content = b"{}"

            def json(self):
                return {}

        mock_post.return_value = _Resp()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"video-bytes")
            tmp_path = tmp.name
        try:
            result = publish_to_upload_post(
                video_path=tmp_path,
                brand_id=1,
                platforms=["YOUTUBE"],
                title="Titulo teste",
                description_by_platform={"YOUTUBE": "desc"},
                request_id="rq-client-1",
                idempotency_key="idem-client-1",
            )
        finally:
            os.unlink(tmp_path)

        headers = mock_post.call_args.kwargs["headers"]
        data = mock_post.call_args.kwargs["data"]
        self.assertEqual(headers["X-Request-Id"], "rq-client-1")
        self.assertEqual(headers["Idempotency-Key"], "idem-client-1")
        self.assertEqual(headers["X-Idempotency-Key"], "idem-client-1")
        self.assertIn(("request_id", "rq-client-1"), data)
        self.assertEqual(result["request_id"], "rq-client-1")

    @override_settings(UPLOAD_POST_API_KEY="test-key")
    @patch("apps.social.publishers.upload_post.requests.post", side_effect=requests.Timeout("boom"))
    def test_upload_post_timeout_keeps_client_request_id(self, _mock_post: MagicMock):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"video-bytes")
            tmp_path = tmp.name
        try:
            with self.assertRaises(UploadPostPublishError) as ctx:
                publish_to_upload_post(
                    video_path=tmp_path,
                    brand_id=1,
                    platforms=["YOUTUBE"],
                    title="Titulo teste",
                    description_by_platform={"YOUTUBE": "desc"},
                    request_id="rq-client-timeout",
                )
        finally:
            os.unlink(tmp_path)

        self.assertEqual(ctx.exception.request_id, "rq-client-timeout")
