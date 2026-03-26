from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.brands.models import Brand, BrandSocialAccount, Factory
from apps.jobs.models import (
    FactoryPostingSchedule,
    FactoryScheduleRun,
    ScheduledPost,
    VideoInventoryItem,
)


@dataclass
class SlotPlan:
    brand: Brand
    video_type: str  # SHORT | LONG
    scheduled_at: datetime


def _to_local_dt(factory_tz: str, day: date, t: time) -> datetime:
    tz = ZoneInfo(factory_tz)
    return datetime.combine(day, t).replace(tzinfo=tz)


def _parse_time_str(s: str) -> time | None:
    """Converte 'HH:MM' ou 'HH:MM:SS' em time."""
    s = (s or "").strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return time(h, m)
        except (ValueError, TypeError):
            pass
    return None


def _build_slots_from_fixed_times(
    *,
    brand: Brand,
    day: date,
    factory_tz: str,
    slot_times: list[str],
    now_local: datetime,
) -> list[datetime]:
    """Gera slots a partir de horários fixos (ex: ['10:00', '14:00', '18:00'])."""
    slots: list[datetime] = []
    for s in slot_times:
        t = _parse_time_str(s)
        if t is None:
            continue
        dt = _to_local_dt(factory_tz, day, t)
        if dt >= now_local:
            slots.append(dt)
    return sorted(slots)


def _all_slots_for_day(
    *,
    brand: Brand,
    day: date,
    factory_tz: str,
    slot_times: list[str],
) -> list[datetime]:
    """Todos os horários configurados do dia (ignora se já passaram). Usado com enqueue_immediately."""
    slots: list[datetime] = []
    for s in slot_times:
        t = _parse_time_str(s)
        if t is None:
            continue
        dt = _to_local_dt(factory_tz, day, t)
        slots.append(dt)
    return sorted(slots)


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
) -> dict:
    """
    Gera agenda de postagens para uma factory.
    target_date: dia para o qual gerar os slots. Se None, usa o dia local atual.
    brand_id: quando informado, agenda apenas para essa brand (dentro da factory).
    enqueue_immediately: se True (ex.: botão "agendamento imediato"), ScheduledPost fica com
    scheduled_at = agora para o próximo ciclo do Beat enfileirar; mantém horários de slot em
    FactoryPostingSchedule para deduplicação e calendário.
    """
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

    created_count = 0
    day_start_local = datetime.combine(local_day, time(0, 0)).replace(tzinfo=tz)
    day_end_local = (day_start_local + timedelta(days=1)) - timedelta(microseconds=1)
    day_start_utc = day_start_local.astimezone(UTC)
    day_end_utc = day_end_local.astimezone(UTC)
    brands_qs = Brand.objects.filter(factory=factory).order_by("id")
    if brand_id:
        brands_qs = brands_qs.filter(id=brand_id)
    brands = list(brands_qs)
    DEFAULT_SHORT_SLOTS = ["10:00", "14:00", "18:00"]
    DEFAULT_LONG_SLOTS = ["20:00"]

    for brand in brands:
        plans: list[SlotPlan] = []
        short_slot_times = getattr(brand, "short_slot_times", None) or []
        if not isinstance(short_slot_times, list):
            short_slot_times = []
        if not short_slot_times:
            short_slot_times = DEFAULT_SHORT_SLOTS
        long_slot_times = getattr(brand, "long_slot_times", None) or []
        if not isinstance(long_slot_times, list):
            long_slot_times = []
        if not long_slot_times:
            long_slot_times = DEFAULT_LONG_SLOTS
        if enqueue_immediately:
            short_slots = (
                _all_slots_for_day(
                    brand=brand,
                    day=local_day,
                    factory_tz=factory.timezone,
                    slot_times=short_slot_times,
                )
                if short_slot_times
                else []
            )
            long_slots = (
                _all_slots_for_day(
                    brand=brand,
                    day=local_day,
                    factory_tz=factory.timezone,
                    slot_times=long_slot_times,
                )
                if long_slot_times
                else []
            )
        else:
            short_slots = _build_slots_from_fixed_times(
                brand=brand,
                day=local_day,
                factory_tz=factory.timezone,
                slot_times=short_slot_times,
                now_local=now_local,
            ) if short_slot_times else []
            long_slots = _build_slots_from_fixed_times(
                brand=brand,
                day=local_day,
                factory_tz=factory.timezone,
                slot_times=long_slot_times,
                now_local=now_local,
            ) if long_slot_times else []
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
                p.scheduled_at.astimezone(UTC),
            ) not in occupied
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

        for plan in plans:
            queue = short_queue if plan.video_type == "SHORT" else long_queue
            if not queue:
                continue
            item = queue.pop(0)
            platform = "YT" if plan.video_type == "SHORT" else "YTB"
            account = _first_social_account_for_video_type(brand, plan.video_type)
            slot_at_utc = plan.scheduled_at.astimezone(UTC)
            post_at_utc = now_utc if enqueue_immediately else slot_at_utc
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
            FactoryPostingSchedule.objects.create(
                factory=factory,
                brand=brand,
                inventory_item=item,
                video_type=plan.video_type,
                scheduled_at=slot_at_utc,
                status="PLANNED",
                scheduled_post=scheduled_post,
            )
            item.status = "SCHEDULED"
            item.scheduled_for = post_at_utc
            item.save(update_fields=["status", "scheduled_for", "updated_at"])
            created_count += 1
    return {"factory_id": factory.id, "created": created_count, "run_id": run.id}
