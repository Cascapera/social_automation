"""Cálculo da janela efetiva do dia (base + jitter) e validações."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .channel_schedule_config import ChannelScheduleConfig


@dataclass(frozen=True)
class EffectiveWindow:
    """Janela local [start, end] para o dia informado."""

    day: date
    timezone: str
    window_start_local: datetime
    window_end_local: datetime

    @property
    def window_start_utc(self) -> datetime:
        return self.window_start_local.astimezone(UTC)

    @property
    def window_end_utc(self) -> datetime:
        return self.window_end_local.astimezone(UTC)


def _clamp_time_to_day(day: date, tz: ZoneInfo, t: time) -> datetime:
    return datetime.combine(day, t).replace(tzinfo=tz)


def compute_effective_window(
    *,
    cfg: ChannelScheduleConfig,
    day: date,
    rng,
) -> tuple[EffectiveWindow | None, str | None]:
    """
    Aplica jitter opcional aos limites base (minutos somados/subtraídos).
    Retorna (None, motivo) se a janela for inválida após jitter.
    """
    tz = ZoneInfo(cfg.timezone)
    base_start = _clamp_time_to_day(day, tz, cfg.base_start_time)
    base_end = _clamp_time_to_day(day, tz, cfg.base_end_time)
    if base_end <= base_start:
        return None, "base_end_time deve ser posterior a base_start_time"

    # Jitter: desloca início para a direita e fim para a esquerda (reduz janela), com aleatoriedade.
    sj = min(cfg.start_jitter_minutes, int((base_end - base_start).total_seconds() // 120))
    ej = min(cfg.end_jitter_minutes, int((base_end - base_start).total_seconds() // 120))
    if sj > 0:
        base_start = base_start + timedelta(minutes=int(rng.randint(0, sj)))
    if ej > 0:
        base_end = base_end - timedelta(minutes=int(rng.randint(0, ej)))

    if base_end <= base_start:
        return None, "janela efetiva inválida após jitter (muito estreita)"

    return (
        EffectiveWindow(
            day=day,
            timezone=cfg.timezone,
            window_start_local=base_start,
            window_end_local=base_end,
        ),
        None,
    )


def max_posts_for_window(*, window_minutes: int, min_gap_minutes: int) -> int:
    """Limite superior simples: espaço para (n-1) gaps mínimos entre n posts."""
    if window_minutes <= 0 or min_gap_minutes <= 0:
        return 0
    # (n-1) * g_min <= W  =>  n <= 1 + W // g_min
    return 1 + (window_minutes // min_gap_minutes)


def window_length_minutes(win: EffectiveWindow) -> int:
    delta = win.window_end_local - win.window_start_local
    return max(0, int(delta.total_seconds() // 60))
