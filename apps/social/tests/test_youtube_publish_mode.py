"""Tests for YouTubePublisher._resolve_publish_mode (random direct-public)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.social.publishers.youtube import YouTubePublisher


def _make_post_with_future_schedule():
    post = MagicMock()
    post.scheduled_at = timezone.now() + timedelta(hours=2)
    return post


class ResolvePublishModeTests(SimpleTestCase):
    def test_shorts_always_use_publish_at(self):
        publisher = YouTubePublisher()
        post = _make_post_with_future_schedule()
        account = MagicMock()
        account.platform = "YT"
        with patch("apps.social.publishers.youtube.random.random", return_value=0.0):
            publish_at, privacy = publisher._resolve_publish_mode(post, account)
        self.assertIsNotNone(publish_at)
        self.assertIsNone(privacy)

    def test_long_goes_direct_public_when_rng_below_threshold(self):
        publisher = YouTubePublisher()
        post = _make_post_with_future_schedule()
        account = MagicMock()
        account.platform = "YTB"
        with patch("apps.social.publishers.youtube.random.random", return_value=0.1):
            publish_at, privacy = publisher._resolve_publish_mode(post, account)
        self.assertIsNone(publish_at)
        self.assertEqual(privacy, "public")

    def test_long_uses_publish_at_when_rng_above_threshold(self):
        publisher = YouTubePublisher()
        post = _make_post_with_future_schedule()
        account = MagicMock()
        account.platform = "YTB"
        with patch("apps.social.publishers.youtube.random.random", return_value=0.99):
            publish_at, privacy = publisher._resolve_publish_mode(post, account)
        self.assertIsNotNone(publish_at)
        self.assertIsNone(privacy)

    def test_no_scheduled_at_returns_no_publish_at(self):
        publisher = YouTubePublisher()
        post = MagicMock()
        post.scheduled_at = None
        account = MagicMock()
        account.platform = "YTB"
        publish_at, privacy = publisher._resolve_publish_mode(post, account)
        self.assertIsNone(publish_at)
        self.assertIsNone(privacy)
