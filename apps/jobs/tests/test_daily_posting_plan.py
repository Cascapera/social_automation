"""Testes do plano diário e janela operacional."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from apps.brands.models import Brand, Factory
from apps.jobs.models import DailyPostingPlan, DailyPostingPlanItem
from apps.jobs.services.channel_schedule_config import ChannelScheduleConfig
from apps.jobs.services.daily_posting_plan_service import DailyPostingPlanService, _rng_for_brand_day
from apps.jobs.services.posting_window_calculator import (
    compute_effective_window,
    max_posts_for_window,
    window_length_minutes,
)


class PostingWindowCalculatorTests(TestCase):
    def test_effective_window_within_base(self):
        factory = Factory.objects.create(name="F1", timezone="America/Sao_Paulo")
        brand = Brand.objects.create(name="B1", slug="b1", factory=factory)
        brand.base_start_time = time(9, 0)
        brand.base_end_time = time(18, 0)
        brand.start_jitter_minutes = 0
        brand.end_jitter_minutes = 0
        brand.save()
        cfg = ChannelScheduleConfig.from_brand(brand)
        rng = _rng_for_brand_day(brand.id, date(2026, 4, 2))
        win, err = compute_effective_window(cfg=cfg, day=date(2026, 4, 2), rng=rng)
        self.assertIsNone(err)
        assert win is not None
        self.assertGreaterEqual(win.window_start_local.hour, 9)
        self.assertLessEqual(win.window_end_local.hour, 18)
        wmin = window_length_minutes(win)
        self.assertGreater(wmin, 0)

    def test_jitter_reduces_window(self):
        factory = Factory.objects.create(name="F2", timezone="America/Sao_Paulo")
        brand = Brand.objects.create(name="B2", slug="b2", factory=factory)
        brand.base_start_time = time(10, 0)
        brand.base_end_time = time(12, 0)
        brand.start_jitter_minutes = 30
        brand.end_jitter_minutes = 30
        brand.save()
        cfg = ChannelScheduleConfig.from_brand(brand)
        rng = _rng_for_brand_day(brand.id, date(2026, 4, 2))
        win, err = compute_effective_window(cfg=cfg, day=date(2026, 4, 2), rng=rng)
        self.assertIsNone(err)
        assert win is not None
        self.assertLessEqual(
            (win.window_end_local - win.window_start_local).total_seconds(),
            2 * 3600,
        )


class DailyPostingPlanServiceTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="FA", timezone="America/Sao_Paulo")
        self.brand = Brand.objects.create(name="BA", slug="ba", factory=self.factory)
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

    def test_skipped_weekday(self):
        self.brand.active_weekdays = [0]  # só segunda
        self.brand.save()
        d = date(2026, 4, 2)  # quinta-feira
        self.assertEqual(d.weekday(), 3)
        plan = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(plan.status, DailyPostingPlan.Status.SKIPPED)
        self.assertEqual(plan.items.count(), 0)

    def test_generated_plan_idempotent(self):
        d = date(2026, 4, 6)  # segunda
        p1 = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(p1.status, DailyPostingPlan.Status.GENERATED)
        n1 = p1.items.count()
        self.assertGreaterEqual(n1, 2)
        self.assertLessEqual(n1, 3)
        p2 = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(p2.items.count(), n1)

    def test_gaps_and_window_bounds(self):
        d = date(2026, 4, 6)
        plan = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(plan.status, DailyPostingPlan.Status.GENERATED)
        items = list(plan.items.order_by("order_index"))
        tz = timezone.get_current_timezone()
        for i in range(1, len(items)):
            prev = items[i - 1].scheduled_at.astimezone(tz)
            cur = items[i].scheduled_at.astimezone(tz)
            delta_min = (cur - prev).total_seconds() / 60
            self.assertGreaterEqual(delta_min, self.brand.min_gap_minutes - 0.5)
            self.assertLessEqual(delta_min, self.brand.max_gap_minutes + 1)

    def test_long_count_within_bounds(self):
        d = date(2026, 4, 7)
        plan = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(plan.status, DailyPostingPlan.Status.GENERATED)
        longs = plan.items.filter(video_type="LONG").count()
        self.assertLessEqual(longs, self.brand.daily_max_long_posts)

    def test_invalid_window_errors(self):
        self.brand.base_start_time = time(20, 0)
        self.brand.base_end_time = time(8, 0)  # inválido (antes do start no mesmo dia)
        self.brand.save()
        d = date(2026, 4, 6)
        plan = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(plan.status, DailyPostingPlan.Status.ERROR)

    def test_max_posts_cap_by_window(self):
        self.brand.base_start_time = time(10, 0)
        self.brand.base_end_time = time(10, 30)  # 30 min
        self.brand.daily_min_posts = 1
        self.brand.daily_max_posts = 10
        self.brand.min_gap_minutes = 20
        self.brand.save()
        d = date(2026, 4, 6)
        plan = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        # 30 min, gap min 20 => no máximo 2 posts
        if plan.status == DailyPostingPlan.Status.GENERATED:
            self.assertLessEqual(plan.items.count(), 2)
        else:
            self.assertEqual(plan.status, DailyPostingPlan.Status.ERROR)

    def test_scheduler_paused_skips(self):
        self.brand.scheduler_paused = True
        self.brand.save()
        d = date(2026, 4, 6)
        plan = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(plan.status, DailyPostingPlan.Status.SKIPPED)
        self.assertEqual(plan.items.count(), 0)

    def test_yesterday_penalty_weight(self):
        """Penalidade de repetição não quebra a escolha."""
        d = date(2026, 4, 6)
        DailyPostingPlan.objects.create(
            brand=self.brand,
            plan_date=date(2026, 4, 5),
            timezone="America/Sao_Paulo",
            status=DailyPostingPlan.Status.GENERATED,
            planned_posts_count=3,
        )
        plan = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(plan.status, DailyPostingPlan.Status.GENERATED)
        self.assertGreaterEqual(plan.planned_posts_count, 2)

    def test_long_slot_anchors_long_near_configured_hour(self):
        """Com long_slot_times preenchido, o longo fica ancorado perto do horário (± jitter)."""
        self.brand.daily_min_long_posts = 1
        self.brand.daily_max_long_posts = 1
        self.brand.long_slot_times = ["18:00"]
        self.brand.save()
        d = date(2026, 4, 6)
        plan = DailyPostingPlanService.get_or_generate_for_day(self.brand, d)
        self.assertEqual(plan.status, DailyPostingPlan.Status.GENERATED)
        long_items = [i for i in plan.items.all() if i.video_type == "LONG"]
        self.assertEqual(len(long_items), 1)
        local = long_items[0].scheduled_at.astimezone(ZoneInfo("America/Sao_Paulo"))
        # Jitter ± até ~20 min em torno de 18:00
        minutes_from_midnight = local.hour * 60 + local.minute
        self.assertGreaterEqual(minutes_from_midnight, 17 * 60 + 40)
        self.assertLessEqual(minutes_from_midnight, 18 * 60 + 20)


class MaxPostsForWindowTests(TestCase):
    def test_max_posts_formula(self):
        self.assertEqual(max_posts_for_window(window_minutes=120, min_gap_minutes=60), 3)
