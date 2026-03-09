from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.brands.models import Factory, Brand, BrandSocialAccount
from apps.jobs.models import (
    FactoryScheduleRun,
    FactoryPostingSchedule,
    VideoInventoryItem,
    ScheduledPost,
)


@dataclass
class SlotPlan:
    brand: Brand
    video_type: str  # SHORT | LONG
    scheduled_at: datetime


def _to_local_dt(factory_tz: str, day: date, t: time) -> datetime:
    tz = ZoneInfo(factory_tz)
    return datetime.combine(day, t).replace(tzinfo=tz)


def _build_slots(
    *,
    brand: Brand,
    day: date,
    factory_tz: str,
    video_type: str,
    start_time: time | None,
    end_time: time | None,
    interval_minutes: int,
    max_per_day: int,
    now_local: datetime,
) -> list[datetime]:
    if not start_time or not end_time or max_per_day <= 0:
        return []
    if start_time >= end_time:
        return []
    interval = max(1, int(interval_minutes or 1))
    start_dt = _to_local_dt(factory_tz, day, start_time)
    end_dt = _to_local_dt(factory_tz, day, end_time)
    slots: list[datetime] = []
    cursor = start_dt
    while cursor <= end_dt and len(slots) < max_per_day:
        if cursor >= now_local:
            slots.append(cursor)
        cursor = cursor + timedelta(minutes=interval)
    return slots


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
) -> dict:
    now_utc = now_utc or timezone.now()
    tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
    now_local = now_utc.astimezone(tz)
    local_day = now_local.date()
    run, created = FactoryScheduleRun.objects.get_or_create(
        factory=factory,
        run_date=local_day,
        defaults={"timezone": factory.timezone or "America/Sao_Paulo"},
    )
    if not created and not allow_rerun:
        return {"factory_id": factory.id, "created": 0, "skipped": "already_generated"}

    created_count = 0
    day_start_local = datetime.combine(local_day, time(0, 0)).replace(tzinfo=tz)
    day_end_local = (day_start_local + timedelta(days=1)) - timedelta(microseconds=1)
    day_start_utc = day_start_local.astimezone(dt_timezone.utc)
    day_end_utc = day_end_local.astimezone(dt_timezone.utc)
    brands = Brand.objects.filter(factory=factory).order_by("id")
    for brand in brands:
        plans: list[SlotPlan] = []
        short_slots = _build_slots(
            brand=brand,
            day=local_day,
            factory_tz=factory.timezone,
            video_type="SHORT",
            start_time=brand.short_window_start,
            end_time=brand.short_window_end,
            interval_minutes=brand.min_short_interval_minutes,
            max_per_day=brand.max_shorts_per_day,
            now_local=now_local,
        )
        long_slots = _build_slots(
            brand=brand,
            day=local_day,
            factory_tz=factory.timezone,
            video_type="LONG",
            start_time=brand.long_window_start,
            end_time=brand.long_window_end,
            interval_minutes=brand.min_long_interval_minutes,
            max_per_day=brand.max_longs_per_day,
            now_local=now_local,
        )
        for dt in short_slots:
            plans.append(SlotPlan(brand=brand, video_type="SHORT", scheduled_at=dt))
        for dt in long_slots:
            plans.append(SlotPlan(brand=brand, video_type="LONG", scheduled_at=dt))
        plans.sort(key=lambda p: p.scheduled_at)

        # Evita duplicar slots já planejados no mesmo dia (permite reruns intradiários).
        occupied = set(
            FactoryPostingSchedule.objects.filter(
                factory=factory,
                brand=brand,
                scheduled_at__gte=day_start_utc,
                scheduled_at__lte=day_end_utc,
            ).values_list("video_type", "scheduled_at")
        )
        plans = [
            p for p in plans
            if (
                p.video_type,
                p.scheduled_at.astimezone(dt_timezone.utc),
            ) not in occupied
        ]

        short_items = list(
            VideoInventoryItem.objects.select_for_update()
            .filter(factory=factory, brand=brand, status="AVAILABLE", video_type="SHORT")
            .order_by("id")
        )
        long_items = list(
            VideoInventoryItem.objects.select_for_update()
            .filter(factory=factory, brand=brand, status="AVAILABLE", video_type="LONG")
            .order_by("id")
        )
        short_queue = _order_with_source_diversity(short_items)
        long_queue = _order_with_source_diversity(long_items)

        for plan in plans:
            queue = short_queue if plan.video_type == "SHORT" else long_queue
            if not queue:
                continue
            item = queue.pop(0)
            platform = "YT" if plan.video_type == "SHORT" else "YTB"
            account = _first_social_account_for_video_type(brand, plan.video_type)
            scheduled_post = ScheduledPost.objects.create(
                job=None,
                auto_cut_corte=item.auto_cut_corte,
                platforms=[platform],
                social_account=account,
                scheduled_at=plan.scheduled_at.astimezone(dt_timezone.utc),
                title=(item.title or "")[:200],
                description=item.description or "",
                privacy_status="private",
                status="PENDING",
            )
            FactoryPostingSchedule.objects.create(
                factory=factory,
                brand=brand,
                inventory_item=item,
                video_type=plan.video_type,
                scheduled_at=plan.scheduled_at.astimezone(dt_timezone.utc),
                status="PLANNED",
                scheduled_post=scheduled_post,
            )
            item.status = "SCHEDULED"
            # scheduled_for só é preenchido quando o YouTube confirmar o envio (em _sync_factory_posting_status)
            item.save(update_fields=["status", "updated_at"])
            created_count += 1
    return {"factory_id": factory.id, "created": created_count, "run_id": run.id}
