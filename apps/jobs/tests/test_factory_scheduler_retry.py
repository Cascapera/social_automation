"""Testa o retry por brand em generate_daily_schedule_for_factory."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import patch

from django.test import TestCase

from apps.brands.models import Brand, Factory
from apps.jobs.models import DailyPostingPlan, DailyPostingPlanItem
from apps.jobs.services import factory_scheduler
from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory


class FactoryScheduleRetryTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="FR", timezone="America/Sao_Paulo")
        self.brand = Brand.objects.create(name="BR", slug="br", factory=self.factory)
        self.brand.base_start_time = time(8, 0)
        self.brand.base_end_time = time(22, 0)
        self.brand.daily_min_posts = 2
        self.brand.daily_max_posts = 3
        self.brand.daily_min_long_posts = 0
        self.brand.daily_max_long_posts = 1
        self.brand.min_gap_minutes = 30
        self.brand.max_gap_minutes = 120
        self.brand.active_weekdays = [0, 1, 2, 3, 4, 5, 6]
        self.brand.scheduler_enabled = True
        self.brand.scheduler_paused = False
        self.brand.save()

    def test_retry_recovers_brand_that_failed_first_attempt(self):
        target = date(2026, 4, 20)
        now_utc = datetime(2026, 4, 20, 11, 0, tzinfo=UTC)

        original = factory_scheduler.DailyPostingPlanService.get_or_generate_for_day
        calls: list[int] = []

        def flaky(brand, day, *, correlation_id=None, force_regenerate=False, attempt=0):
            calls.append(attempt)
            if attempt == 0:
                plan, _ = DailyPostingPlan.objects.get_or_create(
                    brand=brand,
                    plan_date=day,
                    defaults={
                        "timezone": "America/Sao_Paulo",
                        "status": DailyPostingPlan.Status.ERROR,
                        "planned_posts_count": 0,
                        "last_error": "falha_ao_distribuir_shorts_em_segmento",
                    },
                )
                plan.status = DailyPostingPlan.Status.ERROR
                plan.last_error = "falha_ao_distribuir_shorts_em_segmento"
                plan.save()
                return plan
            return original(
                brand,
                day,
                correlation_id=correlation_id,
                force_regenerate=force_regenerate,
                attempt=attempt,
            )

        with patch.object(
            factory_scheduler.DailyPostingPlanService,
            "get_or_generate_for_day",
            side_effect=flaky,
        ):
            result = generate_daily_schedule_for_factory(
                self.factory,
                now_utc=now_utc,
                target_date=target,
                allow_rerun=True,
                correlation_id="test-cid",
            )

        self.assertEqual(result["retried_recovered"], 1)
        self.assertEqual(result["retried_exhausted"], 0)
        self.assertIn(0, calls)
        self.assertIn(1, calls)
        plan = DailyPostingPlan.objects.get(brand=self.brand, plan_date=target)
        self.assertEqual(plan.status, DailyPostingPlan.Status.GENERATED)
        items_total = (
            plan.items.filter(status=DailyPostingPlanItem.Status.PLANNED).count()
            + plan.items.filter(status=DailyPostingPlanItem.Status.CONSUMED).count()
        )
        self.assertGreater(items_total, 0)

    def test_retry_exhausted_marks_brand_as_failed(self):
        target = date(2026, 4, 20)
        now_utc = datetime(2026, 4, 20, 11, 0, tzinfo=UTC)

        def always_error(brand, day, *, correlation_id=None, force_regenerate=False, attempt=0):
            plan, _ = DailyPostingPlan.objects.get_or_create(
                brand=brand,
                plan_date=day,
                defaults={
                    "timezone": "America/Sao_Paulo",
                    "status": DailyPostingPlan.Status.ERROR,
                    "planned_posts_count": 0,
                    "last_error": "falha_persistente",
                },
            )
            plan.status = DailyPostingPlan.Status.ERROR
            plan.last_error = "falha_persistente"
            plan.save()
            return plan

        with patch.object(
            factory_scheduler.DailyPostingPlanService,
            "get_or_generate_for_day",
            side_effect=always_error,
        ):
            result = generate_daily_schedule_for_factory(
                self.factory,
                now_utc=now_utc,
                target_date=target,
                allow_rerun=True,
                correlation_id="test-cid",
            )

        self.assertEqual(result["retried_recovered"], 0)
        self.assertEqual(result["retried_exhausted"], 1)
