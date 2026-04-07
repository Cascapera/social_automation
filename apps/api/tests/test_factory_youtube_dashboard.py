"""Dashboard YouTube (Upload Post) por factory: agregação e endpoint."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.brands.models import Brand, Factory
from apps.jobs.models import Job, ScheduledPost

User = get_user_model()


class FactoryYoutubeDashboardAggregationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory_a = Factory.objects.create(name="F-YT-A", is_active=True)
        self.factory_b = Factory.objects.create(name="F-YT-B", is_active=True)
        self.b1 = Brand.objects.create(name="B1", slug="b1-yt", factory=self.factory_a)
        self.b2 = Brand.objects.create(name="B2", slug="b2-yt", factory=self.factory_a)
        self.other = Brand.objects.create(name="Other", slug="other-yt", factory=self.factory_b)

    @patch("apps.api.factory_youtube_dashboard.fetch_post_analytics")
    @patch("apps.api.factory_youtube_dashboard.fetch_total_impressions")
    @patch("apps.api.factory_youtube_dashboard.fetch_profile_platforms_analytics")
    @patch("apps.api.factory_youtube_dashboard.get_upload_post_api_key")
    def test_only_brands_from_factory(
        self,
        mock_key,
        mock_prof,
        mock_tot,
        mock_post,
    ):
        mock_key.return_value = "test-key"
        mock_prof.return_value = (
            {"youtube": {"followers": 100, "reach_timeseries": [{"date": "2026-01-01", "value": 5}]}},
            None,
        )
        mock_tot.return_value = (
            {
                "success": True,
                "metrics": {"views": 10, "likes": 2, "comments": 1, "shares": 0, "video_count": 1},
                "per_day": {"2026-01-01": 10},
            },
            None,
        )
        mock_post.return_value = (None, None)

        from apps.api.factory_youtube_dashboard import build_factory_youtube_dashboard

        data = build_factory_youtube_dashboard(self.factory_a.id, period="last_month")
        ids = {b["brand_id"] for b in data["brands"]}
        self.assertEqual(ids, {self.b1.id, self.b2.id})
        self.assertNotIn(self.other.id, ids)
        self.assertTrue(data["meta"].get("has_period_metrics"))
        self.assertTrue(data["meta"].get("has_subscriber_data"))

    @patch("apps.api.factory_youtube_dashboard.get_upload_post_api_key")
    def test_no_api_key_stable_payload(self, mock_key):
        mock_key.return_value = ""
        from apps.api.factory_youtube_dashboard import build_factory_youtube_dashboard

        data = build_factory_youtube_dashboard(self.factory_a.id)
        self.assertIn("summary", data)
        self.assertIn("meta", data)
        self.assertIn("config_error", data["meta"])
        self.assertEqual(data["brands"], [])

    @patch("apps.api.factory_youtube_dashboard.fetch_post_analytics")
    @patch("apps.api.factory_youtube_dashboard.fetch_total_impressions")
    @patch("apps.api.factory_youtube_dashboard.fetch_profile_platforms_analytics")
    @patch("apps.api.factory_youtube_dashboard.get_upload_post_api_key")
    def test_partial_brand_failure_does_not_break(
        self,
        mock_key,
        mock_prof,
        mock_tot,
        mock_post,
    ):
        mock_key.return_value = "test-key"
        mock_prof.side_effect = [
            ({"youtube": {"followers": 50}}, None),
            (None, "404 not found"),
        ]
        mock_tot.side_effect = [
            ({"success": True, "metrics": {"views": 5, "video_count": 1}}, None),
            (None, "skip"),
        ]
        mock_post.return_value = (None, None)

        from apps.api.factory_youtube_dashboard import build_factory_youtube_dashboard

        cache.clear()
        data = build_factory_youtube_dashboard(self.factory_a.id, period="last_month")
        self.assertEqual(len(data["brands"]), 2)
        errs = [b.get("error") for b in data["brands"]]
        self.assertTrue(any(errs))


class FactoryYoutubeDashboardEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="yt-dash", password="pass12345")
        self.factory = Factory.objects.create(name="F-API-YT", is_active=True)
        Brand.objects.create(name="B-API", slug="b-api-yt", factory=self.factory)

    def test_endpoint_404_unknown_factory(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        r = client.get("/api/dashboard/factory/999999/youtube-summary/")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    @patch("apps.api.factory_youtube_dashboard.build_factory_youtube_dashboard")
    def test_endpoint_ok(self, mock_build):
        mock_build.return_value = {"scope": {"factory_id": self.factory.id}, "summary": {}, "brands": []}
        client = APIClient()
        client.force_authenticate(user=self.user)
        r = client.get(f"/api/dashboard/factory/{self.factory.id}/youtube-summary/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["scope"]["factory_id"], self.factory.id)
