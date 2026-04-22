"""Tests for YouTubePublisher._post_pinned_first_comment."""

from __future__ import annotations

from unittest.mock import MagicMock

from django.test import SimpleTestCase
from googleapiclient.errors import HttpError

from apps.social.publishers.youtube import YouTubePublisher


def _make_post(first_comment: str | None) -> MagicMock:
    suggestion = MagicMock()
    suggestion.raw_data = {"suggested_first_comment": first_comment} if first_comment is not None else {}
    corte = MagicMock()
    corte.suggestion = suggestion
    post = MagicMock()
    post.auto_cut_corte_id = 123
    post.auto_cut_corte = corte
    return post


class PostPinnedFirstCommentTests(SimpleTestCase):
    def test_skips_when_no_corte(self):
        publisher = YouTubePublisher()
        youtube = MagicMock()
        post = MagicMock()
        post.auto_cut_corte_id = None
        publisher._post_pinned_first_comment(youtube, "vid123", post)
        youtube.commentThreads.assert_not_called()

    def test_skips_when_text_missing(self):
        publisher = YouTubePublisher()
        youtube = MagicMock()
        post = _make_post(first_comment=None)
        publisher._post_pinned_first_comment(youtube, "vid123", post)
        youtube.commentThreads.assert_not_called()

    def test_skips_when_text_blank(self):
        publisher = YouTubePublisher()
        youtube = MagicMock()
        post = _make_post(first_comment="   ")
        publisher._post_pinned_first_comment(youtube, "vid123", post)
        youtube.commentThreads.assert_not_called()

    def test_posts_comment_with_correct_body(self):
        publisher = YouTubePublisher()
        youtube = MagicMock()
        post = _make_post(first_comment="Olá pessoal, o que acharam?")
        publisher._post_pinned_first_comment(youtube, "vid123", post)
        youtube.commentThreads.return_value.insert.assert_called_once()
        _, kwargs = youtube.commentThreads.return_value.insert.call_args
        self.assertEqual(kwargs["part"], "snippet")
        snippet = kwargs["body"]["snippet"]
        self.assertEqual(snippet["videoId"], "vid123")
        self.assertEqual(
            snippet["topLevelComment"]["snippet"]["textOriginal"],
            "Olá pessoal, o que acharam?",
        )

    def test_http_error_does_not_raise(self):
        publisher = YouTubePublisher()
        youtube = MagicMock()
        resp = MagicMock()
        resp.status = 403
        youtube.commentThreads.return_value.insert.return_value.execute.side_effect = HttpError(
            resp, b'{"error":{"message":"comments disabled"}}'
        )
        post = _make_post(first_comment="Teste")
        publisher._post_pinned_first_comment(youtube, "vid123", post)

    def test_generic_error_does_not_raise(self):
        publisher = YouTubePublisher()
        youtube = MagicMock()
        youtube.commentThreads.return_value.insert.return_value.execute.side_effect = RuntimeError("boom")
        post = _make_post(first_comment="Teste")
        publisher._post_pinned_first_comment(youtube, "vid123", post)


class ScheduleFirstCommentTests(SimpleTestCase):
    """Cobre o enfileiramento assincrono via Celery."""

    def test_skips_when_post_has_no_id(self):
        from unittest.mock import patch

        publisher = YouTubePublisher()
        post = _make_post(first_comment="Algum texto")
        post.id = None
        with patch("apps.social.tasks.post_youtube_first_comment_task") as task:
            publisher._schedule_pinned_first_comment("vid123", post)
            task.apply_async.assert_not_called()

    def test_skips_when_text_missing(self):
        from unittest.mock import patch

        publisher = YouTubePublisher()
        post = _make_post(first_comment=None)
        post.id = 42
        with patch("apps.social.tasks.post_youtube_first_comment_task") as task:
            publisher._schedule_pinned_first_comment("vid123", post)
            task.apply_async.assert_not_called()

    def test_enqueues_with_countdown_in_range(self):
        from unittest.mock import patch

        publisher = YouTubePublisher()
        post = _make_post(first_comment="Olá!")
        post.id = 42
        with patch("apps.social.tasks.post_youtube_first_comment_task") as task:
            publisher._schedule_pinned_first_comment("vid123", post)
            task.apply_async.assert_called_once()
            _, kwargs = task.apply_async.call_args
            self.assertEqual(kwargs["args"], [42, "vid123"])
            self.assertIn("countdown", kwargs)
            self.assertGreaterEqual(kwargs["countdown"], 30)
            self.assertLessEqual(kwargs["countdown"], 180)
