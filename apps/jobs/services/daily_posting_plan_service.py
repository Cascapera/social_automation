"""Geração idempotente do plano diário de postagens por Brand."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction

from apps.brands.models import Brand
from apps.jobs.logging_utils import log_event
from apps.jobs.models import DailyPostingPlan, DailyPostingPlanItem

from .channel_schedule_config import ChannelScheduleConfig
from .posting_window_calculator import (
    compute_effective_window,
    max_posts_for_window,
    window_length_minutes,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Penalidade suave: evita repetir o mesmo volume vários dias seguidos (não é regra rígida).
_REPEAT_PENALTY_WEIGHT = 0.35


def _rng_for_brand_day(brand_id: int, day: date):
    import random

    payload = f"{brand_id}:{day.isoformat()}:{getattr(settings, 'SECRET_KEY', '') or 'no-secret'}"
    h = hashlib.sha256(payload.encode("utf-8")).digest()
    seed = int.from_bytes(h[:8], "big")
    return random.Random(seed)


def _yesterday_planned_count(brand_id: int, day: date) -> int | None:
    from datetime import timedelta as td

    prev = day - td(days=1)
    row = (
        DailyPostingPlan.objects.filter(brand_id=brand_id, plan_date=prev, status=DailyPostingPlan.Status.GENERATED)
        .values_list("planned_posts_count", flat=True)
        .first()
    )
    if row is None:
        return None
    return int(row)


def _pick_target_total(cfg: ChannelScheduleConfig, brand_id: int, day: date, rng) -> int:
    lo = max(0, cfg.daily_min_posts)
    hi = max(lo, cfg.daily_max_posts)
    if hi == 0:
        return 0
    candidates = list(range(lo, hi + 1))
    if not candidates:
        return 0
    yesterday = _yesterday_planned_count(brand_id, day)
    weights = []
    for c in candidates:
        w = 1.0
        if yesterday is not None and c == yesterday:
            w *= 1.0 - _REPEAT_PENALTY_WEIGHT
        weights.append(w)
    total_w = sum(weights)
    r = rng.random() * total_w
    acc = 0.0
    for c, w in zip(candidates, weights, strict=True):
        acc += w
        if r <= acc:
            return c
    return candidates[-1]


def _pick_long_count(cfg: ChannelScheduleConfig, target_total: int, rng) -> int:
    if target_total <= 0:
        return 0
    lo = min(cfg.daily_min_long_posts, target_total)
    hi = min(cfg.daily_max_long_posts, target_total)
    lo = max(0, min(lo, hi))
    if hi <= 0:
        return 0
    return int(rng.randint(lo, hi))


def _generate_uneven_times(
    *,
    start: datetime,
    end: datetime,
    n: int,
    min_gap_minutes: int,
    max_gap_minutes: int,
    rng,
) -> list[datetime] | None:
    """Gera n horários em [start, end] com gaps variáveis e leve não-uniformidade."""
    if n <= 0:
        return []
    if end <= start:
        return None
    total_min = int((end - start).total_seconds() // 60)
    if n == 1:
        jitter = rng.randint(0, max(0, total_min))
        return [start + timedelta(minutes=jitter)]

    g_min = max(1, min_gap_minutes)
    g_max = max(g_min, max_gap_minutes)
    # Espaço mínimo necessário para n posts: (n-1) * g_min
    if (n - 1) * g_min > total_min:
        return None

    # Partições aleatórias do intervalo [0, total_min]: n pontos ordenados com restrição de gap mínimo.
    # Começamos com n offsets uniformes com ruído, depois projetamos para respeitar min/max gap.
    raw = []
    for i in range(n):
        base = (i + 0.5) * total_min / (n + 0.0)
        noise = rng.uniform(-total_min / (4.0 * n), total_min / (4.0 * n))
        raw.append(max(0.0, min(float(total_min), base + noise)))
    raw.sort()
    # Garantir min gap
    fixed = [raw[0]]
    for i in range(1, n):
        prev = fixed[-1]
        nxt = max(raw[i], prev + g_min)
        fixed.append(min(float(total_min), nxt))
    # Se estourou, comprimir da direita para esquerda
    for _ in range(3):
        if fixed[-1] <= total_min:
            break
        overflow = fixed[-1] - total_min
        for i in range(n - 1, 0, -1):
            max_back = fixed[i] - fixed[i - 1] - g_min
            dec = min(overflow, max(0.0, max_back))
            fixed[i] -= dec
            overflow -= dec
            if overflow <= 0:
                break
        if fixed[-1] > total_min:
            return None
    # Aplicar teto de gap máximo onde possível (expandir jitter interno)
    times = [start + timedelta(minutes=int(round(x))) for x in fixed]
    # Ajuste fino: garantir ordem e gaps
    times[0] = max(times[0], start)
    for i in range(1, n):
        min_t = times[i - 1] + timedelta(minutes=g_min)
        max_t = times[i - 1] + timedelta(minutes=g_max)
        candidate = times[i]
        if candidate < min_t:
            candidate = min_t
        if candidate > max_t:
            candidate = max_t
        if candidate > end:
            return None
        times[i] = candidate
    if times[-1] > end:
        return None
    return times


def _assign_video_types(n: int, long_count: int, rng) -> list[str]:
    types = ["SHORT"] * n
    if long_count <= 0:
        return types
    long_count = min(long_count, n)
    idxs = list(range(n))
    rng.shuffle(idxs)
    for i in idxs[:long_count]:
        types[i] = "LONG"
    return types


def _parse_long_slot_time(s: str) -> time | None:
    s = (s or "").strip()
    if not s:
        return None
    parts = s.replace(".", ":").split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        sec = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return None
    h %= 24
    m = min(max(m, 0), 59)
    sec = min(max(sec, 0), 59)
    return time(h, m, sec)


def _long_slot_strings_from_brand(brand: Brand) -> list[str]:
    raw = getattr(brand, "long_slot_times", None) or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _long_anchor_jitter_minutes(cfg: ChannelScheduleConfig) -> int:
    """Jitter ± ao redor do horário fixo de longo (varia dia a dia, determinístico pelo rng do dia)."""
    return max(3, min(20, (cfg.min_gap_minutes or 30) // 2))


def _build_long_anchored_datetimes(
    *,
    day: date,
    tz: ZoneInfo,
    win_start: datetime,
    win_end: datetime,
    long_n: int,
    slot_strings: list[str],
    jitter_min: int,
    min_gap_minutes: int,
    rng,
) -> list[datetime] | None:
    parsed: list[time] = []
    for s in slot_strings:
        t = _parse_long_slot_time(s)
        if t is not None:
            parsed.append(t)
    if not parsed:
        return None
    parsed.sort(key=lambda tt: (tt.hour, tt.minute, tt.second))
    long_dts: list[datetime] = []
    for i in range(long_n):
        t = parsed[i % len(parsed)]
        base = datetime.combine(day, t, tzinfo=tz)
        j = rng.randint(-jitter_min, jitter_min) if jitter_min > 0 else 0
        dt = base + timedelta(minutes=j)
        if dt < win_start:
            dt = win_start
        elif dt > win_end:
            dt = win_end
        long_dts.append(dt)
    long_dts.sort()
    g = timedelta(minutes=max(1, min_gap_minutes))
    for i in range(1, len(long_dts)):
        if long_dts[i] < long_dts[i - 1] + g:
            long_dts[i] = long_dts[i - 1] + g
    if long_dts[0] < win_start:
        delta = win_start - long_dts[0]
        long_dts = [d + delta for d in long_dts]
    if long_dts[-1] > win_end:
        return None
    return long_dts


def _free_segments_for_shorts(
    win_start: datetime,
    win_end: datetime,
    long_sorted: list[datetime],
    min_gap_minutes: int,
) -> list[tuple[datetime, datetime]]:
    g = timedelta(minutes=max(1, min_gap_minutes))
    if not long_sorted:
        if win_end > win_start:
            return [(win_start, win_end)]
        return []
    segs: list[tuple[datetime, datetime]] = []
    if long_sorted[0] - win_start >= g:
        segs.append((win_start, long_sorted[0] - g))
    for i in range(len(long_sorted) - 1):
        a = long_sorted[i] + g
        b = long_sorted[i + 1] - g
        if b > a:
            segs.append((a, b))
    if win_end - long_sorted[-1] >= g:
        segs.append((long_sorted[-1] + g, win_end))
    return segs


def _max_posts_in_segment(start: datetime, end: datetime, min_gap_minutes: int) -> int:
    if end <= start:
        return 0
    wmin = int((end - start).total_seconds() // 60)
    return max_posts_for_window(window_minutes=wmin, min_gap_minutes=min_gap_minutes)


def _allocate_short_counts(
    weights: list[int], short_n: int, caps: list[int], rng
) -> list[int] | None:
    n = len(caps)
    if short_n == 0:
        return [0] * n
    if sum(caps) < short_n:
        return None
    counts = [0] * n
    for _ in range(short_n):
        choices = [i for i in range(n) if counts[i] < caps[i]]
        if not choices:
            return None
        i = max(choices, key=lambda j: (weights[j] / (1 + counts[j]), rng.random()))
        counts[i] += 1
    return counts


def _generate_times_with_long_anchors(
    *,
    win_start: datetime,
    win_end: datetime,
    target_total: int,
    long_n: int,
    slot_strings: list[str],
    day: date,
    tzname: str,
    cfg: ChannelScheduleConfig,
    rng,
) -> tuple[list[tuple[datetime, str]], str | None]:
    """
    Longos ancorados nos horários configurados (± jitter); shorts nos intervalos livres
    entre longos, respeitando min_gap.
    """
    tz = ZoneInfo(tzname)
    short_n = target_total - long_n
    jitter = _long_anchor_jitter_minutes(cfg)
    long_dts = _build_long_anchored_datetimes(
        day=day,
        tz=tz,
        win_start=win_start,
        win_end=win_end,
        long_n=long_n,
        slot_strings=slot_strings,
        jitter_min=jitter,
        min_gap_minutes=cfg.min_gap_minutes,
        rng=rng,
    )
    if long_dts is None:
        return None, "longos_nao_cabem_na_janela_com_gaps"
    long_sorted = sorted(long_dts)
    if short_n == 0:
        return [(t, "LONG") for t in long_sorted], None

    segs = _free_segments_for_shorts(win_start, win_end, long_sorted, cfg.min_gap_minutes)
    weights = [max(0, int((b - a).total_seconds() // 60)) for a, b in segs]
    caps = [_max_posts_in_segment(a, b, cfg.min_gap_minutes) for a, b in segs]
    if sum(caps) < short_n:
        return None, "janela_insuficiente_para_shorts_apos_longos"
    counts = _allocate_short_counts(weights, short_n, caps, rng)
    if counts is None:
        return None, "falha_alocar_shorts_entre_segmentos"

    short_dts: list[datetime] = []
    for (a, b), k in zip(segs, counts, strict=True):
        if k <= 0:
            continue
        part = _generate_uneven_times(
            start=a,
            end=b,
            n=k,
            min_gap_minutes=cfg.min_gap_minutes,
            max_gap_minutes=cfg.max_gap_minutes,
            rng=rng,
        )
        if part is None or len(part) != k:
            return None, "falha_ao_distribuir_shorts_em_segmento"
        short_dts.extend(part)

    merged = [(t, "LONG") for t in long_sorted] + [(t, "SHORT") for t in short_dts]
    merged.sort(key=lambda x: x[0])
    chain = [m[0] for m in merged]
    if not _verify_sorted_chain_min_gap(chain, cfg.min_gap_minutes):
        return None, "gaps_invalidos_apos_merge_long_short"
    return merged, None


def _verify_sorted_chain_min_gap(times: list[datetime], min_gap_minutes: int) -> bool:
    if len(times) < 2:
        return True
    g = timedelta(minutes=max(1, min_gap_minutes))
    for i in range(1, len(times)):
        if times[i] - times[i - 1] < g:
            return False
    return True


class DailyPostingPlanService:
    """Get-or-create seguro do plano diário (sem recriar destrutivamente)."""

    @staticmethod
    @transaction.atomic
    def get_or_generate_for_day(
        brand: Brand,
        day: date,
        *,
        correlation_id: str | None = None,
        force_regenerate: bool = False,
    ) -> DailyPostingPlan:
        """
        Retorna o plano existente se já estiver GENERADO (idempotente).
        Se force_regenerate=True, apaga itens anteriores apenas quando o plano não tiver itens CONSUMED.
        """
        rng = _rng_for_brand_day(brand.id, day)
        cfg = ChannelScheduleConfig.from_brand(brand)

        plan = (
            DailyPostingPlan.objects.select_for_update()
            .filter(brand=brand, plan_date=day)
            .first()
        )

        if plan and not force_regenerate:
            if plan.status == DailyPostingPlan.Status.GENERATED and plan.items.exists():
                log_event(
                    logger,
                    event="daily_plan_reused",
                    correlation_id=correlation_id,
                    brand_id=brand.id,
                    plan_id=plan.id,
                    plan_date=str(day),
                    status="reused",
                )
                return plan
            if plan.status == DailyPostingPlan.Status.SKIPPED:
                log_event(
                    logger,
                    event="daily_plan_reused",
                    correlation_id=correlation_id,
                    brand_id=brand.id,
                    plan_id=plan.id,
                    plan_date=str(day),
                    status="skipped_reused",
                )
                return plan

        if plan and force_regenerate and plan.items.filter(status=DailyPostingPlanItem.Status.CONSUMED).exists():
            log_event(
                logger,
                event="daily_plan_regenerate_blocked",
                correlation_id=correlation_id,
                brand_id=brand.id,
                plan_id=plan.id,
                reason="has_consumed_items",
            )
            return plan

        if plan and force_regenerate:
            plan.items.all().delete()

        if not plan:
            from django.db import IntegrityError

            try:
                plan = DailyPostingPlan.objects.create(
                    brand=brand,
                    plan_date=day,
                    timezone=cfg.timezone,
                    status=DailyPostingPlan.Status.DRAFT,
                    planned_posts_count=0,
                    config_snapshot={},
                )
            except IntegrityError:
                plan = DailyPostingPlan.objects.select_for_update().get(brand=brand, plan_date=day)
            else:
                plan = DailyPostingPlan.objects.select_for_update().get(pk=plan.pk)
        elif plan.status == DailyPostingPlan.Status.ERROR:
            plan.items.all().delete()

        snap = cfg.to_snapshot_dict()
        plan.timezone = cfg.timezone
        plan.config_snapshot = snap
        plan.generated_at = datetime.now(UTC)
        plan.last_error = ""

        if not cfg.enabled or cfg.paused:
            plan.status = DailyPostingPlan.Status.SKIPPED
            plan.planned_posts_count = 0
            plan.save()
            log_event(
                logger,
                event="daily_plan_skipped",
                correlation_id=correlation_id,
                brand_id=brand.id,
                plan_date=str(day),
                reason="disabled_or_paused",
            )
            return plan

        if day.weekday() not in cfg.active_weekdays:
            plan.status = DailyPostingPlan.Status.SKIPPED
            plan.planned_posts_count = 0
            plan.save()
            log_event(
                logger,
                event="daily_plan_skipped",
                correlation_id=correlation_id,
                brand_id=brand.id,
                plan_date=str(day),
                reason="weekday_not_active",
            )
            return plan

        win, err = compute_effective_window(cfg=cfg, day=day, rng=rng)
        if err or win is None:
            plan.status = DailyPostingPlan.Status.ERROR
            plan.planned_posts_count = 0
            plan.last_error = err or "invalid_window"
            plan.save()
            log_event(
                logger,
                event="daily_plan_error",
                correlation_id=correlation_id,
                brand_id=brand.id,
                plan_date=str(day),
                error=plan.last_error,
            )
            return plan

        wmin = window_length_minutes(win)
        max_fit = max_posts_for_window(window_minutes=wmin, min_gap_minutes=cfg.min_gap_minutes)

        target_total = _pick_target_total(cfg, brand.id, day, rng)
        if target_total > max_fit:
            target_total = max_fit
        if target_total < cfg.daily_min_posts and cfg.daily_min_posts <= max_fit:
            target_total = cfg.daily_min_posts
        if target_total > cfg.daily_max_posts:
            target_total = cfg.daily_max_posts
        if target_total > max_fit:
            target_total = max_fit
        if target_total <= 0:
            plan.status = DailyPostingPlan.Status.ERROR
            plan.planned_posts_count = 0
            plan.last_error = "janela_operacional_insuficiente_para_min_posts"
            plan.save()
            log_event(
                logger,
                event="daily_plan_error",
                correlation_id=correlation_id,
                brand_id=brand.id,
                plan_date=str(day),
                error=plan.last_error,
                window_minutes=wmin,
                max_fit=max_fit,
            )
            return plan

        long_n = _pick_long_count(cfg, target_total, rng)
        long_n = min(long_n, target_total)

        slot_strings = _long_slot_strings_from_brand(brand)
        use_long_anchors = bool(slot_strings) and long_n > 0

        if use_long_anchors:
            merged, merge_err = _generate_times_with_long_anchors(
                win_start=win.window_start_local,
                win_end=win.window_end_local,
                target_total=target_total,
                long_n=long_n,
                slot_strings=slot_strings,
                day=day,
                tzname=cfg.timezone,
                cfg=cfg,
                rng=rng,
            )
            if merge_err or merged is None:
                plan.status = DailyPostingPlan.Status.ERROR
                plan.planned_posts_count = 0
                plan.last_error = merge_err or "falha_plano_com_ancoras_longas"
                plan.save()
                log_event(
                    logger,
                    event="daily_plan_error",
                    correlation_id=correlation_id,
                    brand_id=brand.id,
                    plan_date=str(day),
                    error=plan.last_error,
                )
                return plan
            pairs = merged
        else:
            times = _generate_uneven_times(
                start=win.window_start_local,
                end=win.window_end_local,
                n=target_total,
                min_gap_minutes=cfg.min_gap_minutes,
                max_gap_minutes=cfg.max_gap_minutes,
                rng=rng,
            )
            if times is None or len(times) != target_total:
                plan.status = DailyPostingPlan.Status.ERROR
                plan.planned_posts_count = 0
                plan.last_error = "falha_ao_distribuir_horarios_na_janela"
                plan.save()
                log_event(
                    logger,
                    event="daily_plan_error",
                    correlation_id=correlation_id,
                    brand_id=brand.id,
                    plan_date=str(day),
                    error=plan.last_error,
                )
                return plan

            types = _assign_video_types(target_total, long_n, rng)
            pairs = list(zip(times, types, strict=True))

        plan.status = DailyPostingPlan.Status.GENERATED
        plan.planned_posts_count = target_total
        plan.save()

        DailyPostingPlanItem.objects.filter(plan=plan).delete()
        items = []
        for i, (dt, vt) in enumerate(pairs):
            at_utc = dt.astimezone(UTC)
            items.append(
                DailyPostingPlanItem(
                    plan=plan,
                    order_index=i,
                    video_type=vt,
                    scheduled_at=at_utc,
                    status=DailyPostingPlanItem.Status.PLANNED,
                )
            )
        DailyPostingPlanItem.objects.bulk_create(items)

        log_event(
            logger,
            event="daily_plan_generated",
            correlation_id=correlation_id,
            brand_id=brand.id,
            plan_id=plan.id,
            plan_date=str(day),
            planned_posts=target_total,
            long_posts=sum(1 for _, vt in pairs if vt == "LONG"),
            status="success",
        )
        return DailyPostingPlan.objects.select_related("brand").prefetch_related("items").get(pk=plan.pk)
