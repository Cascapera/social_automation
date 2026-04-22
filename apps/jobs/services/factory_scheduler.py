from __future__ import annotations

import logging
import random
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
from apps.social.services.title_humanizer import humanize_title

# Jitter aplicado ao slot para nao postar sempre no mesmo minuto exato.
SLOT_JITTER_MIN_SECONDS = -180  # -3 min
SLOT_JITTER_MAX_SECONDS = 420  # +7 min


def _compute_slot_jitter_seconds() -> int:
    """Retorna jitter aleatorio em segundos. Extraido para permitir mock em testes."""
    return random.randint(SLOT_JITTER_MIN_SECONDS, SLOT_JITTER_MAX_SECONDS)

logger = logging.getLogger(__name__)

# Retentativas adicionais quando uma brand termina com plano em ERROR no run.
# Total de tentativas = 1 + MAX_SCHEDULE_RETRIES.
MAX_SCHEDULE_RETRIES = 2


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


def _available_inventory_items_for_slot(
    *,
    factory: Factory,
    brand: Brand,
    video_type: str,
    exclude_item_ids: set[int] | None = None,
) -> list[VideoInventoryItem]:
    qs = (
        VideoInventoryItem.objects.select_for_update()
        .filter(factory=factory, brand=brand, status="AVAILABLE", video_type=video_type)
        .exclude(auto_cut_corte_id__isnull=True)
        .order_by("id")
    )
    if exclude_item_ids:
        qs = qs.exclude(id__in=sorted(exclude_item_ids))
    return list(qs)


def pick_inventory_item_for_slot(
    *,
    factory: Factory,
    brand: Brand,
    video_type: str,
    exclude_item_ids: set[int] | None = None,
) -> VideoInventoryItem | None:
    items = _available_inventory_items_for_slot(
        factory=factory,
        brand=brand,
        video_type=video_type,
        exclude_item_ids=exclude_item_ids,
    )
    if not items:
        return None
    ordered = _order_with_source_diversity(items)
    return ordered[0] if ordered else None


def _extract_tags_from_inventory(item: VideoInventoryItem) -> list[str]:
    """
    Le tags geradas pelo LLM em AutoCutSuggestion.raw_data['tags'].
    Retorna lista lowercase, deduplicada, max 15 itens, cada tag <=100 chars.
    """
    corte = getattr(item, "auto_cut_corte", None)
    suggestion = getattr(corte, "suggestion", None) if corte else None
    raw = getattr(suggestion, "raw_data", None) or {}
    tags = raw.get("tags")
    if not isinstance(tags, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in tags:
        s = str(t or "").strip().lower()
        if not s or len(s) > 100 or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
        if len(cleaned) >= 15:
            break
    return cleaned


def allocate_inventory_item_to_slot(
    *,
    factory: Factory,
    brand: Brand,
    item: VideoInventoryItem,
    video_type: str,
    scheduled_at: datetime,
    schedule: FactoryPostingSchedule | None = None,
    plan_item: DailyPostingPlanItem | None = None,
    correlation_id: str = "",
    external_ids: dict | None = None,
) -> tuple[ScheduledPost, FactoryPostingSchedule]:
    platform = "YT" if video_type == "SHORT" else "YTB"
    account = _first_social_account_for_video_type(brand, video_type)
    slot_at_utc = scheduled_at.astimezone(UTC)
    jitter_seconds = _compute_slot_jitter_seconds()
    jittered_at = slot_at_utc + timedelta(seconds=jitter_seconds)
    min_floor = timezone.now().astimezone(UTC) + timedelta(seconds=60)
    if jittered_at < min_floor:
        jittered_at = min_floor
    slot_at_utc = jittered_at
    tags = _extract_tags_from_inventory(item)
    merged_external_ids = dict(external_ids or {})
    merged_external_ids["slot_jitter_seconds"] = jitter_seconds
    humanized_title = humanize_title(item.title or "")[:200]
    scheduled_post = ScheduledPost.objects.create(
        job=None,
        auto_cut_corte=item.auto_cut_corte,
        platforms=[platform],
        social_account=account,
        scheduled_at=slot_at_utc,
        title=humanized_title,
        description=item.description or "",
        tags=tags,
        privacy_status="private",
        status="PENDING",
        external_ids=merged_external_ids,
        correlation_id=correlation_id or "",
    )

    target_schedule = schedule
    if target_schedule is None:
        fps_kwargs = {
            "factory": factory,
            "brand": brand,
            "inventory_item": item,
            "video_type": video_type,
            "scheduled_at": slot_at_utc,
            "status": "PLANNED",
            "scheduled_post": scheduled_post,
        }
        if plan_item:
            fps_kwargs["daily_plan_item"] = plan_item
        target_schedule = FactoryPostingSchedule.objects.create(**fps_kwargs)
    else:
        target_schedule.inventory_item = item
        target_schedule.video_type = video_type
        target_schedule.scheduled_at = slot_at_utc
        target_schedule.status = "PLANNED"
        target_schedule.scheduled_post = scheduled_post
        target_schedule.attempt_count = 0
        target_schedule.next_retry_at = None
        if plan_item is not None:
            target_schedule.daily_plan_item = plan_item
        target_schedule.save(
            update_fields=[
                "inventory_item",
                "video_type",
                "scheduled_at",
                "status",
                "scheduled_post",
                "attempt_count",
                "next_retry_at",
                "daily_plan_item",
                "updated_at",
            ]
        )

    target_plan_item = plan_item or getattr(target_schedule, "daily_plan_item", None)
    if target_plan_item:
        DailyPostingPlanItem.objects.filter(pk=target_plan_item.pk).update(
            status=DailyPostingPlanItem.Status.CONSUMED,
            inventory_item_id=item.id,
            scheduled_post_id=scheduled_post.id,
        )

    item.status = "SCHEDULED"
    item.scheduled_for = slot_at_utc
    item.save(update_fields=["status", "scheduled_for", "updated_at"])
    return scheduled_post, target_schedule


def _schedule_brand_for_day(
    *,
    factory: Factory,
    brand: Brand,
    local_day: date,
    tz: ZoneInfo,
    now_local: datetime,
    day_start_utc: datetime,
    day_end_utc: datetime,
    enqueue_immediately: bool,
    correlation_id: str | None,
    attempt: int,
) -> tuple[str, int]:
    """
    Processa uma brand para o dia informado.
    Retorna (status, created_count):
      status = "ok" | "skipped" | "disabled" | "error".
    """
    if not getattr(brand, "scheduler_enabled", True) or getattr(brand, "scheduler_paused", False):
        log_event(
            logger,
            event="brand_schedule_skipped",
            correlation_id=correlation_id,
            brand_id=brand.id,
            factory_id=factory.id,
            reason="scheduler_disabled_or_paused",
        )
        return "disabled", 0

    day_plan = DailyPostingPlanService.get_or_generate_for_day(
        brand,
        local_day,
        correlation_id=correlation_id,
        force_regenerate=False,
        attempt=attempt,
    )
    if day_plan.status == DailyPostingPlan.Status.SKIPPED:
        return "skipped", 0
    if day_plan.status == DailyPostingPlan.Status.ERROR:
        log_event(
            logger,
            event="brand_schedule_no_plan_items",
            correlation_id=correlation_id,
            brand_id=brand.id,
            plan_id=day_plan.id,
            plan_status=day_plan.status,
            last_error=(day_plan.last_error or "")[:200],
            attempt=attempt,
        )
        return "error", 0

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
            daily_plan_item__isnull=False,
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

    short_items = _available_inventory_items_for_slot(
        factory=factory,
        brand=brand,
        video_type="SHORT",
    )
    long_items = _available_inventory_items_for_slot(
        factory=factory,
        brand=brand,
        video_type="LONG",
    )
    short_queue = _order_with_source_diversity(short_items)
    long_queue = _order_with_source_diversity(long_items)

    created_count = 0
    for slot_plan in plans:
        queue = short_queue if slot_plan.video_type == "SHORT" else long_queue
        if not queue:
            continue
        item = queue.pop(0)
        allocate_inventory_item_to_slot(
            factory=factory,
            brand=brand,
            item=item,
            video_type=slot_plan.video_type,
            scheduled_at=slot_plan.scheduled_at,
            plan_item=slot_plan.plan_item,
        )
        created_count += 1

    return "ok", created_count


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

    failed_brands: list[Brand] = []
    for brand in brands:
        status, count = _schedule_brand_for_day(
            factory=factory,
            brand=brand,
            local_day=local_day,
            tz=tz,
            now_local=now_local,
            day_start_utc=day_start_utc,
            day_end_utc=day_end_utc,
            enqueue_immediately=enqueue_immediately,
            correlation_id=correlation_id,
            attempt=0,
        )
        created_count += count
        if status == "error":
            failed_brands.append(brand)

    retried_recovered = 0
    retried_exhausted = 0
    for brand in failed_brands:
        recovered = False
        for attempt in range(1, MAX_SCHEDULE_RETRIES + 1):
            log_event(
                logger,
                event="brand_schedule_retry_started",
                correlation_id=correlation_id,
                factory_id=factory.id,
                brand_id=brand.id,
                attempt=attempt,
            )
            status, count = _schedule_brand_for_day(
                factory=factory,
                brand=brand,
                local_day=local_day,
                tz=tz,
                now_local=now_local,
                day_start_utc=day_start_utc,
                day_end_utc=day_end_utc,
                enqueue_immediately=enqueue_immediately,
                correlation_id=correlation_id,
                attempt=attempt,
            )
            if status == "ok":
                created_count += count
                retried_recovered += 1
                recovered = True
                log_event(
                    logger,
                    event="brand_schedule_retry_succeeded",
                    correlation_id=correlation_id,
                    factory_id=factory.id,
                    brand_id=brand.id,
                    attempt=attempt,
                    number_of_posts=count,
                )
                break
            if status in ("skipped", "disabled"):
                break
        if not recovered:
            retried_exhausted += 1
            log_event(
                logger,
                event="brand_schedule_retry_exhausted",
                correlation_id=correlation_id,
                factory_id=factory.id,
                brand_id=brand.id,
                max_attempts=MAX_SCHEDULE_RETRIES + 1,
            )

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
        retried_brand_count=len(failed_brands),
        retried_recovered=retried_recovered,
        retried_exhausted=retried_exhausted,
    )
    return {
        "factory_id": factory.id,
        "created": created_count,
        "run_id": run.id,
        "retried_recovered": retried_recovered,
        "retried_exhausted": retried_exhausted,
    }
