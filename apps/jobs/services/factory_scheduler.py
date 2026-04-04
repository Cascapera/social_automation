from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.brands.models import Brand, BrandSocialAccount, Factory
from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.models import (
    DailyPostingPlan,
    DailyPostingPlanItem,
    FactoryPostingSchedule,
    FactoryScheduleRun,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.jobs.services.daily_posting_plan_service import DailyPostingPlanService

logger = logging.getLogger(__name__)


@dataclass
class SlotPlan:
    brand: Brand
    video_type: str  # SHORT | LONG
    scheduled_at: datetime
    plan_item: DailyPostingPlanItem | None = None


def _order_with_source_diversity(items: list[VideoInventoryItem]) -> list[VideoInventoryItem]:
    """
    Ordena por score asc e evita mais de 2 seguidos do mesmo source_asset_id.
    Fallback: usa o item disponível quando não houver alternativa.
    """
    pool = sorted(items, key=lambda x: ((x.virality_score or 0), x.id))
    ordered: list[VideoInventoryItem] = []
    while pool:
        chosen_index = None
        for i, candidate in enumerate(pool):
            source = (candidate.source_asset_id or "").strip()
            if len(ordered) < 2:
                chosen_index = i
                break
            prev1 = (ordered[-1].source_asset_id or "").strip()
            prev2 = (ordered[-2].source_asset_id or "").strip()
            if not source or source != prev1 or source != prev2:
                chosen_index = i
                break
        if chosen_index is None:
            chosen_index = 0
        ordered.append(pool.pop(chosen_index))
    return ordered


def _first_social_account_for_video_type(brand: Brand, video_type: str) -> BrandSocialAccount | None:
    preferred_platform = "YT" if video_type == "SHORT" else "YTB"
    account = (
        BrandSocialAccount.objects.filter(brand=brand, platform=preferred_platform)
        .order_by("id")
        .first()
    )
    if account:
        return account
    alt = "YTB" if preferred_platform == "YT" else "YT"
    return (
        BrandSocialAccount.objects.filter(brand=brand, platform=alt)
        .order_by("id")
        .first()
    )


@transaction.atomic
def generate_daily_schedule_for_factory(
    factory: Factory,
    now_utc: datetime | None = None,
    *,
    allow_rerun: bool = False,
    target_date: date | None = None,
    brand_id: int | None = None,
    enqueue_immediately: bool = False,
    correlation_id: str | None = None,
) -> dict:
    """
    Gera agenda de postagens para uma factory a partir do plano diário persistido por brand.
    target_date: dia para o qual gerar os slots. Se None, usa o dia local atual.
    brand_id: quando informado, agenda apenas para essa brand (dentro da factory).
    enqueue_immediately: se True (ex.: botão "agendamento imediato"), inclui todos os slots do dia
    (mesmo os que já passaram); ScheduledPost.scheduled_at continua sendo o horário do slot em UTC.
    correlation_id: opaque token propagated from the Celery task for log correlation.
    """
    timer = Timer()
    now_utc = now_utc or timezone.now()
    tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
    now_local = now_utc.astimezone(tz)
    local_day = target_date if target_date is not None else now_local.date()
    run, created = FactoryScheduleRun.objects.get_or_create(
        factory=factory,
        run_date=local_day,
        defaults={"timezone": factory.timezone or "America/Sao_Paulo"},
    )
    if not created and not allow_rerun:
        return {"factory_id": factory.id, "created": 0, "skipped": "already_generated"}

    log_event(
        logger,
        event="schedule_run_started",
        correlation_id=correlation_id,
        factory_id=factory.id,
        schedule_run_id=run.id,
        status="started",
        target_date=str(local_day),
    )

    created_count = 0
    day_start_local = datetime.combine(local_day, time(0, 0)).replace(tzinfo=tz)
    day_end_local = (day_start_local + timedelta(days=1)) - timedelta(microseconds=1)
    day_start_utc = day_start_local.astimezone(UTC)
    day_end_utc = day_end_local.astimezone(UTC)
    brands_qs = Brand.objects.filter(factory=factory).order_by("id")
    if brand_id:
        brands_qs = brands_qs.filter(id=brand_id)
    brands = list(brands_qs)

    for brand in brands:
        if not getattr(brand, "scheduler_enabled", True) or getattr(brand, "scheduler_paused", False):
            log_event(
                logger,
                event="brand_schedule_skipped",
                correlation_id=correlation_id,
                brand_id=brand.id,
                factory_id=factory.id,
                reason="scheduler_disabled_or_paused",
            )
            continue

        day_plan = DailyPostingPlanService.get_or_generate_for_day(
            brand,
            local_day,
            correlation_id=correlation_id,
            force_regenerate=False,
        )
        if day_plan.status == DailyPostingPlan.Status.SKIPPED:
            continue
        if day_plan.status == DailyPostingPlan.Status.ERROR:
            log_event(
                logger,
                event="brand_schedule_no_plan_items",
                correlation_id=correlation_id,
                brand_id=brand.id,
                plan_id=day_plan.id,
                plan_status=day_plan.status,
                last_error=(day_plan.last_error or "")[:200],
            )
            continue

        plans: list[SlotPlan] = []
        for dpi in day_plan.items.filter(status=DailyPostingPlanItem.Status.PLANNED).order_by("order_index", "id"):
            slot_local = dpi.scheduled_at.astimezone(tz)
            if not enqueue_immediately and slot_local < now_local:
                continue
            plans.append(
                SlotPlan(
                    brand=brand,
                    video_type=dpi.video_type,
                    scheduled_at=slot_local,
                    plan_item=dpi,
                )
            )
        plans.sort(key=lambda p: p.scheduled_at)

        occupied = set(
            FactoryPostingSchedule.objects.filter(
                factory=factory,
                brand=brand,
                scheduled_at__gte=day_start_utc,
                scheduled_at__lte=day_end_utc,
            ).values_list("video_type", "scheduled_at")
        )
        plans = [
            p
            for p in plans
            if (
                p.video_type,
                p.scheduled_at.astimezone(UTC),
            )
            not in occupied
        ]

        short_items = list(
            VideoInventoryItem.objects.select_for_update()
            .filter(factory=factory, brand=brand, status="AVAILABLE", video_type="SHORT")
            .exclude(auto_cut_corte_id__isnull=True)
            .order_by("id")
        )
        long_items = list(
            VideoInventoryItem.objects.select_for_update()
            .filter(factory=factory, brand=brand, status="AVAILABLE", video_type="LONG")
            .exclude(auto_cut_corte_id__isnull=True)
            .order_by("id")
        )
        short_queue = _order_with_source_diversity(short_items)
        long_queue = _order_with_source_diversity(long_items)

        for slot_plan in plans:
            queue = short_queue if slot_plan.video_type == "SHORT" else long_queue
            if not queue:
                continue
            item = queue.pop(0)
            platform = "YT" if slot_plan.video_type == "SHORT" else "YTB"
            account = _first_social_account_for_video_type(brand, slot_plan.video_type)
            slot_at_utc = slot_plan.scheduled_at.astimezone(UTC)
            post_at_utc = slot_at_utc
            scheduled_post = ScheduledPost.objects.create(
                job=None,
                auto_cut_corte=item.auto_cut_corte,
                platforms=[platform],
                social_account=account,
                scheduled_at=post_at_utc,
                title=(item.title or "")[:200],
                description=item.description or "",
                privacy_status="private",
                status="PENDING",
            )
            fps_kwargs = {
                "factory": factory,
                "brand": brand,
                "inventory_item": item,
                "video_type": slot_plan.video_type,
                "scheduled_at": slot_at_utc,
                "status": "PLANNED",
                "scheduled_post": scheduled_post,
            }
            if slot_plan.plan_item:
                fps_kwargs["daily_plan_item"] = slot_plan.plan_item
            FactoryPostingSchedule.objects.create(**fps_kwargs)

            if slot_plan.plan_item:
                DailyPostingPlanItem.objects.filter(pk=slot_plan.plan_item.pk).update(
                    status=DailyPostingPlanItem.Status.CONSUMED,
                    inventory_item_id=item.id,
                    scheduled_post_id=scheduled_post.id,
                )

            item.status = "SCHEDULED"
            item.scheduled_for = post_at_utc
            item.save(update_fields=["status", "scheduled_for", "updated_at"])
            created_count += 1

    log_event(
        logger,
        event="scheduled_posts_generated",
        correlation_id=correlation_id,
        factory_id=factory.id,
        schedule_run_id=run.id,
        number_of_posts=created_count,
        status="success",
        duration_ms=timer.elapsed_ms(),
        target_date=str(local_day),
    )
    return {"factory_id": factory.id, "created": created_count, "run_id": run.id}
