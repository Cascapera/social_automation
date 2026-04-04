"""Configuração tipada de agendamento por Brand (scheduler diário)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from apps.brands.models import Brand


@dataclass(frozen=True)
class ChannelScheduleConfig:
    """Snapshot imutável usado pelo gerador de plano e pelo snapshot persistido."""

    timezone: str
    enabled: bool
    paused: bool
    base_start_time: time
    base_end_time: time
    start_jitter_minutes: int
    end_jitter_minutes: int
    daily_min_posts: int
    daily_max_posts: int
    daily_min_long_posts: int
    daily_max_long_posts: int
    min_gap_minutes: int
    max_gap_minutes: int
    active_weekdays: tuple[int, ...]  # 0=segunda … 6=domingo (weekday() do Python)

    @classmethod
    def from_brand(cls, brand: Brand) -> ChannelScheduleConfig:
        tz = (getattr(brand, "scheduler_timezone", "") or "").strip()
        if not tz:
            tz = (brand.factory.timezone if brand.factory_id else "") or "America/Sao_Paulo"
        start = getattr(brand, "base_start_time", None) or time(9, 0)
        end = getattr(brand, "base_end_time", None) or time(21, 0)
        wd = getattr(brand, "active_weekdays", None) or []
        if not isinstance(wd, list):
            wd = []
        weekdays = tuple(int(x) for x in wd) if wd else tuple(range(7))
        min_gap = max(1, int(getattr(brand, "min_gap_minutes", 30) or 1))
        max_gap = max(min_gap, int(getattr(brand, "max_gap_minutes", 240) or min_gap))
        return cls(
            timezone=tz,
            enabled=bool(getattr(brand, "scheduler_enabled", True)),
            paused=bool(getattr(brand, "scheduler_paused", False)),
            base_start_time=start,
            base_end_time=end,
            start_jitter_minutes=max(0, int(getattr(brand, "start_jitter_minutes", 0) or 0)),
            end_jitter_minutes=max(0, int(getattr(brand, "end_jitter_minutes", 0) or 0)),
            daily_min_posts=max(0, int(getattr(brand, "daily_min_posts", 1) or 0)),
            daily_max_posts=max(0, int(getattr(brand, "daily_max_posts", 3) or 0)),
            daily_min_long_posts=max(0, int(getattr(brand, "daily_min_long_posts", 0) or 0)),
            daily_max_long_posts=max(0, int(getattr(brand, "daily_max_long_posts", 1) or 0)),
            min_gap_minutes=min_gap,
            max_gap_minutes=max_gap,
            active_weekdays=weekdays,
        )

    def to_snapshot_dict(self) -> dict:
        return {
            "timezone": self.timezone,
            "enabled": self.enabled,
            "paused": self.paused,
            "base_start_time": self.base_start_time.isoformat(),
            "base_end_time": self.base_end_time.isoformat(),
            "start_jitter_minutes": self.start_jitter_minutes,
            "end_jitter_minutes": self.end_jitter_minutes,
            "daily_min_posts": self.daily_min_posts,
            "daily_max_posts": self.daily_max_posts,
            "daily_min_long_posts": self.daily_min_long_posts,
            "daily_max_long_posts": self.daily_max_long_posts,
            "min_gap_minutes": self.min_gap_minutes,
            "max_gap_minutes": self.max_gap_minutes,
            "active_weekdays": list(self.active_weekdays),
        }
