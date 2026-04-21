"""Dashboard YouTube (Upload Post) por factory: agregação e endpoint."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.brands.models import Brand, Factory
from apps.jobs.models import Job, ScheduledPost
from apps.mediahub.models import SourceVideo

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

    @patch("apps.api.factory_youtube_dashboard.fetch_post_analytics")
    @patch("apps.api.factory_youtube_dashboard.fetch_total_impressions")
    @patch("apps.api.factory_youtube_dashboard.fetch_profile_platforms_analytics")
    @patch("apps.api.factory_youtube_dashboard.get_upload_post_api_key")
    def test_summary_can_filter_selected_brand(
        self,
        mock_key,
        mock_prof,
        mock_tot,
        mock_post,
    ):
        mock_key.return_value = "test-key"
        mock_prof.return_value = ({"youtube": {"followers": 10}}, None)
        mock_tot.return_value = (
            {
                "success": True,
                "metrics": {"views": 20, "likes": 2, "comments": 1, "shares": 0, "video_count": 1},
                "per_day": {"2026-01-01": 20},
            },
            None,
        )
        mock_post.return_value = (None, None)

        from apps.api.factory_youtube_dashboard import build_factory_youtube_dashboard

        cache.clear()
        data = build_factory_youtube_dashboard(
            self.factory_a.id,
            brand_id=self.b1.id,
            period="last_month",
            include_top_posts=False,
        )
        self.assertEqual([b["brand_id"] for b in data["brands"]], [self.b1.id])
        self.assertEqual(data["scope"]["brand_id"], self.b1.id)


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


class FactoryYoutubeVideosEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="yt-videos", password="pass12345")
        self.client.force_authenticate(user=self.user)
        self.fixed_now = timezone.now().replace(microsecond=0)

        self.factory = Factory.objects.create(name="F-VIDEOS", is_active=True)
        self.other_factory = Factory.objects.create(name="F-OTHER-VIDEOS", is_active=True)
        self.brand_a = Brand.objects.create(name="Brand A", slug="brand-a-videos", factory=self.factory)
        self.brand_b = Brand.objects.create(name="Brand B", slug="brand-b-videos", factory=self.factory)
        self.other_brand = Brand.objects.create(
            name="Other Brand",
            slug="other-brand-videos",
            factory=self.other_factory,
        )

        self.top_post = self._create_auto_cut_post(
            brand=self.brand_a,
            published_title="Corte Campeão",
            original_name="Fonte Real do Corte",
            source_title="Título do Source que deve perder para analysis.name",
            request_id="req-top",
            posted_at=self.fixed_now - timedelta(days=2),
        )
        self.job_post = self._create_job_post(
            brand=self.brand_a,
            published_title="Publicado via Job",
            job_name="Job Original",
            request_id="req-job",
            posted_at=self.fixed_now - timedelta(days=3),
        )
        self.old_views_post = self._create_job_post(
            brand=self.brand_a,
            published_title="Gigante Antigo",
            job_name="Job Antigo",
            request_id="req-old-views",
            posted_at=self.fixed_now - timedelta(days=25),
        )
        self.partial_post = self._create_auto_cut_post(
            brand=self.brand_a,
            published_title="Analytics Parcial",
            original_name="Fonte Parcial",
            source_title="Source Parcial",
            request_id="req-missing",
            posted_at=self.fixed_now - timedelta(days=1),
        )
        self.native_post = self._create_auto_cut_post(
            brand=self.brand_b,
            published_title="Nativo sem Analytics",
            original_name="Fonte Nativa",
            source_title="Source Nativa",
            external_video_id="native-video-id",
            posted_at=self.fixed_now - timedelta(days=4),
        )
        self._create_job_post(
            brand=self.other_brand,
            published_title="Outro Escopo",
            job_name="Job Fora",
            request_id="req-other",
            posted_at=self.fixed_now - timedelta(days=1),
        )

    def _create_auto_cut_post(
        self,
        *,
        brand,
        published_title,
        original_name,
        source_title,
        request_id=None,
        external_video_id="",
        posted_at=None,
    ):
        source = SourceVideo.objects.create(
            brand=brand,
            title=source_title,
            file=SimpleUploadedFile("source.mp4", b"fake-video", content_type="video/mp4"),
        )
        analysis = AutoCutAnalysis.objects.create(
            brand=brand,
            source=source,
            name=original_name,
            status="done",
        )
        suggestion = AutoCutSuggestion.objects.create(
            analysis=analysis,
            cut_type="short",
            start_tc="0:00",
            end_tc="0:10",
            title=f"{published_title} sugestão",
        )
        corte = AutoCutCorte.objects.create(
            analysis=analysis,
            suggestion=suggestion,
        )
        ext = {}
        if request_id:
            ext["upload_post_request_id"] = request_id
        if external_video_id:
            ext["YTB"] = external_video_id
        return ScheduledPost.objects.create(
            auto_cut_corte=corte,
            platforms=["YTB"],
            scheduled_at=posted_at or timezone.now(),
            title=published_title,
            status="DONE",
            posted_at=posted_at or timezone.now(),
            external_ids=ext,
        )

    def _create_job_post(
        self,
        *,
        brand,
        published_title,
        job_name,
        request_id=None,
        posted_at=None,
    ):
        job = Job.objects.create(
            brand=brand,
            name=job_name,
            status="DONE",
        )
        ext = {}
        if request_id:
            ext["upload_post_request_id"] = request_id
        return ScheduledPost.objects.create(
            job=job,
            platforms=["YTB"],
            scheduled_at=posted_at or timezone.now(),
            title=published_title,
            status="DONE",
            posted_at=posted_at or timezone.now(),
            external_ids=ext,
        )

    def _build_payload(self, *, ordering="views", brand_id=None, period="last_month"):
        from apps.api.factory_youtube_dashboard import build_factory_youtube_videos

        cache.clear()
        with (
            patch("apps.api.factory_youtube_dashboard.get_upload_post_api_key", return_value="test-key"),
            patch("apps.api.factory_youtube_dashboard.fetch_post_analytics", side_effect=self._mock_post_analytics),
            patch("apps.api.factory_youtube_dashboard.timezone.now", return_value=self.fixed_now),
        ):
            return build_factory_youtube_videos(
                self.factory.id,
                brand_id=brand_id,
                period=period,
                ordering=ordering,
                force_refresh=True,
            )

    def _request_videos(self, *, ordering=None, brand_id=None, page=1, page_size=10, period="last_month"):
        cache.clear()
        params = [f"period={period}", f"page={page}", f"page_size={page_size}"]
        if brand_id:
            params.append(f"brand={brand_id}")
        if ordering:
            params.append(f"ordering={ordering}")
        url = f"/api/dashboard/factory/{self.factory.id}/youtube-videos/?{'&'.join(params)}"
        with (
            patch("apps.api.factory_youtube_dashboard.get_upload_post_api_key", return_value="test-key"),
            patch("apps.api.factory_youtube_dashboard.fetch_post_analytics", side_effect=self._mock_post_analytics),
            patch("apps.api.factory_youtube_dashboard.timezone.now", return_value=self.fixed_now),
        ):
            return self.client.get(url)

    def _mock_post_analytics(self, request_id, platform="youtube"):
        payloads = {
            "req-top": (
                {
                    "platforms": {
                        "youtube": {
                            "post_metrics": {
                                "views": 1000,
                                "likes": 30,
                                "comments": 4,
                                "shares": 2,
                                "impressions": 5000,
                            },
                            "post_url": "https://youtu.be/top-video",
                        }
                    }
                },
                None,
            ),
            "req-job": (
                {
                    "platforms": {
                        "youtube": {
                            "post_metrics": {
                                "views": 400,
                                "likes": 10,
                                "comments": 1,
                                "shares": 0,
                                "impressions": 1800,
                            },
                            "post_url": "https://youtu.be/job-video",
                        }
                    }
                },
                None,
            ),
            "req-old-views": (
                {
                    "platforms": {
                        "youtube": {
                            "post_metrics": {
                                "views": 2000,
                                "likes": 10,
                                "comments": 1,
                                "shares": 0,
                                "impressions": 9000,
                            },
                            "post_url": "https://youtu.be/old-video",
                        }
                    }
                },
                None,
            ),
            "req-missing": (
                {
                    "platforms": {
                        "youtube": {
                            "post_metrics": {
                                "likes": 7,
                            },
                            "post_url": "https://youtu.be/partial-video",
                        }
                    }
                },
                None,
            ),
            "req-other": (
                {
                    "platforms": {
                        "youtube": {
                            "post_metrics": {
                                "views": 9999,
                            }
                        }
                    }
                },
                None,
            ),
        }
        return payloads.get(request_id, (None, "missing"))

    def test_views_per_day_calculation(self):
        payload = self._build_payload(ordering="viral_score")
        by_title = {row["published_title"]: row for row in payload["results"]}
        self.assertAlmostEqual(by_title["Corte Campeão"]["days_since_post"], 2.0, places=2)
        self.assertAlmostEqual(by_title["Corte Campeão"]["views_per_day"], 500.0, places=2)

    def test_engagement_rate_calculation(self):
        payload = self._build_payload(ordering="viral_score")
        by_title = {row["published_title"]: row for row in payload["results"]}
        self.assertEqual(by_title["Corte Campeão"]["engagement_total"], 36)
        self.assertAlmostEqual(by_title["Corte Campeão"]["engagement_rate"], 0.036, places=4)

    def test_viral_score_calculation(self):
        payload = self._build_payload(ordering="viral_score")
        by_title = {row["published_title"]: row for row in payload["results"]}
        self.assertAlmostEqual(by_title["Corte Campeão"]["recency_factor"], 0.9333, places=4)
        self.assertAlmostEqual(by_title["Corte Campeão"]["viral_score"], 59.0, places=1)

    def test_videos_without_analytics_have_null_viral_score(self):
        payload = self._build_payload(ordering="viral_score")
        by_title = {row["published_title"]: row for row in payload["results"]}

        native = by_title["Nativo sem Analytics"]
        self.assertIsNone(native["views_per_day"])
        self.assertIsNone(native["engagement_rate"])
        self.assertIsNone(native["viral_score"])

    def test_ordering_by_viral_score_works_correctly(self):
        payload = self._build_payload(ordering="viral_score")
        self.assertEqual(
            [row["published_title"] for row in payload["results"]],
            [
                "Corte Campeão",
                "Publicado via Job",
                "Gigante Antigo",
                "Analytics Parcial",
                "Nativo sem Analytics",
            ],
        )

    def test_ordering_by_views_still_works_correctly(self):
        payload = self._build_payload(ordering="views")
        self.assertEqual(
            [row["published_title"] for row in payload["results"]],
            [
                "Gigante Antigo",
                "Corte Campeão",
                "Publicado via Job",
                "Analytics Parcial",
                "Nativo sem Analytics",
            ],
        )

    def test_ordering_by_recent_uses_published_at(self):
        payload = self._build_payload(ordering="recent")
        self.assertEqual(
            [row["published_title"] for row in payload["results"]],
            [
                "Analytics Parcial",
                "Corte Campeão",
                "Publicado via Job",
                "Nativo sem Analytics",
                "Gigante Antigo",
            ],
        )

    def test_ordering_by_likes(self):
        payload = self._build_payload(ordering="likes")
        self.assertEqual(
            [row["published_title"] for row in payload["results"]],
            [
                "Corte Campeão",
                "Publicado via Job",
                "Gigante Antigo",
                "Analytics Parcial",
                "Nativo sem Analytics",
            ],
        )

    def test_ordering_by_comments(self):
        payload = self._build_payload(ordering="comments")
        self.assertEqual(
            [row["published_title"] for row in payload["results"]],
            [
                "Corte Campeão",
                "Publicado via Job",
                "Gigante Antigo",
                "Analytics Parcial",
                "Nativo sem Analytics",
            ],
        )

    def test_ordering_by_engagement_total(self):
        payload = self._build_payload(ordering="engagement")
        self.assertEqual(
            [row["published_title"] for row in payload["results"]],
            [
                "Corte Campeão",
                "Publicado via Job",
                "Gigante Antigo",
                "Analytics Parcial",
                "Nativo sem Analytics",
            ],
        )

    def test_ordering_by_engagement_rate(self):
        payload = self._build_payload(ordering="engagement_rate")
        self.assertEqual(
            [row["published_title"] for row in payload["results"]],
            [
                "Corte Campeão",
                "Publicado via Job",
                "Gigante Antigo",
                "Analytics Parcial",
                "Nativo sem Analytics",
            ],
        )

    def test_ordering_by_views_per_day(self):
        payload = self._build_payload(ordering="views_per_day")
        self.assertEqual(
            [row["published_title"] for row in payload["results"]],
            [
                "Corte Campeão",
                "Publicado via Job",
                "Gigante Antigo",
                "Analytics Parcial",
                "Nativo sem Analytics",
            ],
        )

    def test_meta_exposes_expanded_orderings(self):
        payload = self._build_payload(ordering="views")
        available = payload["meta"]["available_orderings"]
        for key in (
            "views",
            "viral_score",
            "recent",
            "likes",
            "comments",
            "engagement",
            "engagement_rate",
            "views_per_day",
        ):
            self.assertIn(key, available)

    def test_video_list_orders_by_views_and_paginates(self):
        r1 = self._request_videos(page=1, page_size=2)
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r1.data["count"], 5)
        self.assertEqual(r1.data["meta"]["ordering"], "views")
        self.assertEqual(
            [row["published_title"] for row in r1.data["results"]],
            ["Gigante Antigo", "Corte Campeão"],
        )
        self.assertIsNotNone(r1.data["next"])

        r2 = self._request_videos(page=2, page_size=2)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["published_title"] for row in r2.data["results"]],
            ["Publicado via Job", "Analytics Parcial"],
        )
        r3 = self._request_videos(page=3, page_size=2)
        self.assertEqual([row["published_title"] for row in r3.data["results"]], ["Nativo sem Analytics"])

    def test_original_title_and_missing_metrics_are_safe(self):
        r = self._request_videos(page=1, page_size=10, ordering="viral_score")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        by_title = {row["published_title"]: row for row in r.data["results"]}

        self.assertEqual(by_title["Corte Campeão"]["original_title"], "Fonte Real do Corte")
        self.assertEqual(by_title["Publicado via Job"]["original_title"], "Job Original")

        partial = by_title["Analytics Parcial"]
        self.assertIsNone(partial["views"])
        self.assertEqual(partial["likes"], 7)
        self.assertEqual(partial["engagement_total"], 7)
        self.assertIsNone(partial["views_per_day"])
        self.assertIsNone(partial["engagement_rate"])
        self.assertIsNone(partial["viral_score"])
        self.assertIsNone(partial["comments"])
        self.assertIsNone(partial["shares"])
        self.assertIsNone(partial["impressions"])

    def test_native_youtube_posts_without_upload_post_analytics_still_render(self):
        r = self._request_videos(brand_id=self.brand_b.id, page_size=10, ordering="viral_score")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["count"], 1)
        row = r.data["results"][0]
        self.assertEqual(row["published_title"], "Nativo sem Analytics")
        self.assertEqual(row["analytics_source"], "youtube_api_no_analytics")
        self.assertEqual(row["analytics_status"], "unavailable")
        self.assertEqual(row["post_url"], "https://www.youtube.com/watch?v=native-video-id")
        self.assertIsNone(row["views"])
        self.assertIsNone(row["viral_score"])
