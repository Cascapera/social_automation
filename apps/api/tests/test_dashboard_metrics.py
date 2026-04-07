"""Testes das métricas agregadas do dashboard (AutoCut)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.auto_cuts.models import (
    AutoCutAnalysis,
    AutoCutCorte,
    AutoCutReadyChunk,
    AutoCutSuggestion,
)
from apps.brands.models import Brand, Factory

User = get_user_model()


class DashboardMetricsAggregationTests(TestCase):
    """Valida contagens e minutos sem depender do PostgreSQL (SQLite em testes)."""

    def setUp(self):
        self.user = User.objects.create_user(username="dash-user", password="securepass1")
        self.factory = Factory.objects.create(name="F-Dash", is_active=True)
        self.brand = Brand.objects.create(name="B-Dash", slug="b-dash", factory=self.factory)
        self.other_brand = Brand.objects.create(name="B-Other", slug="b-other", factory=self.factory)

    def _done_analysis(self, brand, minutes_end=120.0, user=None, status="done"):
        u = user if user is not None else self.user
        a = AutoCutAnalysis.objects.create(
            user=u,
            brand=brand,
            name="Test",
            status=status,
            transcript_segments=[{"start": 0, "end": minutes_end * 60, "text": "x"}],
        )
        return a

    def test_videos_processed_only_done(self):
        self._done_analysis(self.brand, status="done")
        self._done_analysis(self.brand, status="error")
        from apps.api.dashboard_metrics import compute_dashboard_metrics

        m = compute_dashboard_metrics(self.user, self.brand.id, None)
        self.assertEqual(m["videos_processed"], 1)

    def test_finalized_cuts_only_done_analysis_and_flag(self):
        a_done = self._done_analysis(self.brand)
        sug = AutoCutSuggestion.objects.create(
            analysis=a_done,
            cut_type="short",
            start_tc="0:00",
            end_tc="0:10",
        )
        AutoCutCorte.objects.create(analysis=a_done, suggestion=sug, is_finalized=True)
        AutoCutCorte.objects.create(analysis=a_done, suggestion=sug, is_finalized=False)

        a_pending = AutoCutAnalysis.objects.create(
            user=self.user,
            brand=self.brand,
            name="P",
            status="pending",
        )
        sug2 = AutoCutSuggestion.objects.create(
            analysis=a_pending,
            cut_type="short",
            start_tc="0:00",
            end_tc="0:10",
        )
        AutoCutCorte.objects.create(analysis=a_pending, suggestion=sug2, is_finalized=True)

        from apps.api.dashboard_metrics import compute_dashboard_metrics

        m = compute_dashboard_metrics(self.user, self.brand.id, None)
        self.assertEqual(m["finalized_cuts"], 1)

    def test_minutes_from_transcript_max_end(self):
        self._done_analysis(self.brand, minutes_end=2.0)  # end=120s -> 2 min
        from apps.api.dashboard_metrics import compute_dashboard_metrics

        m = compute_dashboard_metrics(self.user, self.brand.id, None)
        self.assertAlmostEqual(m["total_minutes_processed"], 2.0, places=3)

    def test_minutes_from_ready_chunks_sum(self):
        a = self._done_analysis(self.brand, minutes_end=0)
        AutoCutReadyChunk.objects.create(
            analysis=a,
            order_index=0,
            duration_seconds=90.0,
        )
        AutoCutReadyChunk.objects.create(
            analysis=a,
            order_index=1,
            duration_seconds=30.0,
        )
        from apps.api.dashboard_metrics import compute_dashboard_metrics

        m = compute_dashboard_metrics(self.user, self.brand.id, None)
        self.assertAlmostEqual(m["total_minutes_processed"], 2.0, places=3)

    def test_factory_scope_sums_brands(self):
        self._done_analysis(self.brand, minutes_end=1.0)
        self._done_analysis(self.other_brand, minutes_end=1.0)
        from apps.api.dashboard_metrics import compute_dashboard_metrics

        m = compute_dashboard_metrics(self.user, None, self.factory.id)
        self.assertEqual(m["videos_processed"], 2)
        self.assertAlmostEqual(m["total_minutes_processed"], 2.0, places=3)

    def test_auto_fetch_visible_to_user(self):
        AutoCutAnalysis.objects.create(
            user=None,
            brand=self.brand,
            name="Auto",
            status="done",
            transcript_segments=[{"start": 0, "end": 60, "text": "a"}],
        )
        from apps.api.dashboard_metrics import compute_dashboard_metrics

        m = compute_dashboard_metrics(self.user, self.brand.id, None)
        self.assertEqual(m["videos_processed"], 1)

    def test_other_user_private_jobs_excluded(self):
        other = User.objects.create_user(username="other", password="securepass1")
        AutoCutAnalysis.objects.create(
            user=other,
            brand=self.brand,
            name="X",
            status="done",
            transcript_segments=[{"start": 0, "end": 120, "text": "a"}],
        )
        from apps.api.dashboard_metrics import compute_dashboard_metrics

        m = compute_dashboard_metrics(self.user, self.brand.id, None)
        self.assertEqual(m["videos_processed"], 0)


class DashboardMetricsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="api-dash", password="securepass1")
        self.client.force_authenticate(user=self.user)
        self.factory = Factory.objects.create(name="F-API")
        self.brand = Brand.objects.create(name="B-API", slug="b-api", factory=self.factory)

    def test_requires_brand_or_factory(self):
        res = self.client.get("/api/dashboard-metrics/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_200_with_brand(self):
        res = self.client.get(f"/api/dashboard-metrics/?brand={self.brand.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("videos_processed", res.data)
