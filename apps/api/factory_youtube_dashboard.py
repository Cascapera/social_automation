"""
Agrega analytics YouTube (Upload Post) por Factory.

Identificador de perfil no Upload Post: ``brand_<brand_id>`` (mesmo que ``publish_to_upload_post``).

Fontes:
- GET /api/analytics/<profile>?platforms=youtube — assinantes (followers), série temporal ~30 dias
- GET /api/uploadposts/total-impressions/<profile> — métricas por período (views, likes, comentários, vídeos, per_day)
- GET /api/uploadposts/post-analytics/<request_id> — métricas por post (top vídeos)

Comportamento com dados parciais: cada brand pode falhar sem derrubar o payload; erros ficam em ``brands[].error``.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from apps.brands.models import Brand
from apps.jobs.models import ScheduledPost
from apps.social.services.upload_post_analytics_client import (
    fetch_post_analytics,
    fetch_profile_platforms_analytics,
    fetch_total_impressions,
    get_upload_post_api_key,
)

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600
POST_ANALYTICS_CACHE_TTL_SECONDS = 600
POST_ANALYTICS_ERROR_CACHE_TTL_SECONDS = 120
ALLOWED_PERIODS = frozenset({"last_day", "last_week", "last_month", "last_3months", "last_year"})
PERIOD_TO_DAYS = {
    "last_day": 1,
    "last_week": 7,
    "last_month": 30,
    "last_3months": 90,
    "last_year": 365,
}
VIDEO_ORDERING_ALIASES = {
    "views": "views",
    "views_desc": "views",
    "-views": "views",
    "viral_score": "viral_score",
    "viral_score_desc": "viral_score",
    "-viral_score": "viral_score",
}
VIRAL_SCORE_WEIGHTS = {
    "views_per_day": 0.6,
    "engagement_rate": 0.25,
    "recency_factor": 0.15,
}
VIRAL_SCORE_VIEWS_PER_DAY_LOG_CAP = 4.0
VIRAL_SCORE_ENGAGEMENT_RATE_CAP = 0.20
# Pausa extra entre marcas (além do throttle no cliente HTTP) para não disparar 429 em fábricas grandes.
_BRAND_EXTRA_DELAY_SEC = float(os.getenv("UPLOAD_POST_FACTORY_BRAND_DELAY_SEC", "0.15"))


def upload_post_profile_username(brand_id: int) -> str:
    return f"brand_{int(brand_id)}"


def _dashboard_cache_key(
    factory_id: int,
    *,
    brand_id: int | None,
    period: str,
    include_top_posts: bool,
) -> str:
    return (
        f"factory_youtube_dash:{factory_id}:{brand_id or 'all'}:{period}:"
        f"{1 if include_top_posts else 0}"
    )


def _videos_cache_key(factory_id: int, *, brand_id: int | None, period: str) -> str:
    return f"factory_youtube_videos:{factory_id}:{brand_id or 'all'}:{period}"


def _videos_cache_key_with_ordering(
    factory_id: int,
    *,
    brand_id: int | None,
    period: str,
    ordering: str,
) -> str:
    return f"{_videos_cache_key(factory_id, brand_id=brand_id, period=period)}:{ordering}"


def _post_analytics_cache_key(request_id: str, *, platform: str = "youtube") -> str:
    rid = str(request_id or "").strip()
    return f"upload_post_post_analytics:{platform}:{rid}"


def _safe_int(v: Any) -> int:
    if v is None:
        return 0
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def _opt_int(v: Any) -> int | None:
    """Inteiro opcional: None permanece None (evita confundir 'sem dado' com zero)."""
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _merge_per_day_maps(maps: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for m in maps:
        if not m:
            continue
        for k, v in m.items():
            out[str(k)] += _safe_int(v)
    return dict(sorted(out.items()))


def _merge_timeseries_from_profiles(yt_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Soma reach_timeseries (value) por data a partir dos blocos ``youtube`` do profile analytics."""
    by_date: dict[str, int] = defaultdict(int)
    for yt in yt_blocks:
        if not isinstance(yt, dict):
            continue
        ts = yt.get("reach_timeseries") or []
        if not isinstance(ts, list):
            continue
        for pt in ts:
            if not isinstance(pt, dict):
                continue
            d = pt.get("date")
            if not d:
                continue
            by_date[str(d)] += _safe_int(pt.get("value"))
    return [{"date": d, "views": by_date[d]} for d in sorted(by_date.keys())]


def scheduled_post_brand_id(post: ScheduledPost) -> int | None:
    if post.job_id and getattr(post.job, "brand_id", None):
        return int(post.job.brand_id)
    ac = post.auto_cut_corte
    if ac and getattr(ac, "analysis_id", None):
        a = getattr(ac, "analysis", None)
        if a and getattr(a, "brand_id", None):
            return int(a.brand_id)
    return None


def scheduled_post_brand_name(post: ScheduledPost) -> str | None:
    if post.job_id and getattr(post.job, "brand", None):
        name = (getattr(post.job.brand, "name", None) or "").strip()
        if name:
            return name
    ac = post.auto_cut_corte
    if ac and getattr(ac, "analysis", None):
        brand = getattr(ac.analysis, "brand", None)
        name = (getattr(brand, "name", None) or "").strip()
        if name:
            return name
    return None


def _first_non_blank(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _file_stem(name: str | None) -> str | None:
    raw = str(name or "").strip()
    if not raw:
        return None
    base = raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base or None


def scheduled_post_published_title(post: ScheduledPost) -> str | None:
    inventory_item = getattr(getattr(post, "factory_schedule", None), "inventory_item", None)
    suggestion = getattr(getattr(post, "auto_cut_corte", None), "suggestion", None)
    return _first_non_blank(
        post.title,
        getattr(inventory_item, "title", None),
        getattr(suggestion, "title", None),
        getattr(post.job, "name", None) if post.job_id else None,
    )


def scheduled_post_original_title(post: ScheduledPost) -> str | None:
    if post.auto_cut_corte_id:
        analysis = getattr(post.auto_cut_corte, "analysis", None)
        suggestion = getattr(post.auto_cut_corte, "suggestion", None)
        if analysis is not None:
            return _first_non_blank(
                getattr(analysis, "name", None),
                getattr(getattr(analysis, "source", None), "title", None),
                _file_stem(getattr(getattr(analysis, "file", None), "name", None)),
                getattr(suggestion, "title", None),
            )
        return _first_non_blank(getattr(suggestion, "title", None))

    if post.job_id:
        return _first_non_blank(getattr(post.job, "name", None))

    return None


def scheduled_post_external_video_id(post: ScheduledPost) -> str | None:
    ext = post.external_ids or {}
    for key in ("YTB", "YT"):
        value = str(ext.get(key) or "").strip()
        if value:
            return value
    return None


def _youtube_watch_url(video_id: str | None) -> str | None:
    vid = str(video_id or "").strip()
    if not vid:
        return None
    return f"https://www.youtube.com/watch?v={vid}"


def normalize_video_ordering(raw: str | None) -> str:
    key = str(raw or "").strip().lower()
    return VIDEO_ORDERING_ALIASES.get(key, "views")


def _period_window_start(period: str) -> datetime:
    days = PERIOD_TO_DAYS.get(period, PERIOD_TO_DAYS["last_month"])
    return timezone.now() - timedelta(days=days)


def _days_since_post(posted_at: datetime | None) -> float | None:
    if posted_at is None:
        return None
    delta_seconds = max((timezone.now() - posted_at).total_seconds(), 0.0)
    return round(delta_seconds / 86400.0, 2)


def _sum_available_ints(*values: int | None) -> int | None:
    present = [int(v) for v in values if v is not None]
    if not present:
        return None
    return sum(present)


def _views_per_day(views: int | None, days_since_post: float | None) -> float | None:
    if views is None:
        return None
    denom = max(float(days_since_post or 0.0), 1.0)
    return round(float(views) / denom, 2)


def _engagement_rate(engagement_total: int | None, views: int | None) -> float | None:
    if engagement_total is None or views is None:
        return None
    return round(float(engagement_total) / max(float(views), 1.0), 4)


def _recency_factor(days_since_post: float | None, period: str) -> float | None:
    if days_since_post is None:
        return None
    period_days = max(float(PERIOD_TO_DAYS.get(period, PERIOD_TO_DAYS["last_month"])), 1.0)
    factor = 1.0 - min(float(days_since_post), period_days) / period_days
    return round(max(0.0, min(1.0, factor)), 4)


def _views_per_day_component(views_per_day: float | None) -> float | None:
    if views_per_day is None:
        return None
    if views_per_day <= 0:
        return 0.0
    component = math.log10(float(views_per_day) + 1.0) / VIRAL_SCORE_VIEWS_PER_DAY_LOG_CAP
    return max(0.0, min(1.0, component))


def _engagement_rate_component(engagement_rate: float | None) -> float | None:
    if engagement_rate is None:
        return None
    component = float(engagement_rate) / VIRAL_SCORE_ENGAGEMENT_RATE_CAP
    return max(0.0, min(1.0, component))


def _viral_score(
    *,
    views_per_day: float | None,
    engagement_rate: float | None,
    recency_factor: float | None,
) -> float | None:
    if views_per_day is None:
        return None

    components = {
        "views_per_day": _views_per_day_component(views_per_day),
        "engagement_rate": _engagement_rate_component(engagement_rate),
        "recency_factor": recency_factor,
    }
    weighted_sum = 0.0
    total_weight = 0.0
    for name, component in components.items():
        if component is None:
            continue
        weight = VIRAL_SCORE_WEIGHTS[name]
        weighted_sum += weight * float(component)
        total_weight += weight

    if total_weight <= 0:
        return None
    return round((weighted_sum / total_weight) * 100.0, 1)


def _sort_video_rows(rows: list[dict[str, Any]], *, ordering: str) -> list[dict[str, Any]]:
    ordering_norm = normalize_video_ordering(ordering)
    if ordering_norm == "viral_score":
        return sorted(
            rows,
            key=lambda row: (
                row.get("viral_score") is None,
                -(row.get("viral_score") or 0.0),
                -(row.get("_sort_published_ts") or 0.0),
                -(row.get("views") or 0),
                -(row.get("scheduled_post_id") or 0),
            ),
        )

    return sorted(
        rows,
        key=lambda row: (
            row.get("views") is None,
            -(row.get("views") or 0),
            row.get("viral_score") is None,
            -(row.get("viral_score") or 0.0),
            -(row.get("_sort_published_ts") or 0.0),
            -(row.get("scheduled_post_id") or 0),
        ),
    )


def _scheduled_posts_scope_queryset(
    factory_id: int,
    *,
    brand_id: int | None = None,
    posted_after: datetime | None = None,
):
    qs = (
        ScheduledPost.objects.filter(status="DONE", posted_at__isnull=False)
        .filter(
            Q(job__brand__factory_id=factory_id) | Q(auto_cut_corte__analysis__brand__factory_id=factory_id),
        )
        .select_related(
            "job__brand",
            "auto_cut_corte__analysis__brand",
            "auto_cut_corte__analysis__source",
            "auto_cut_corte__suggestion",
            "factory_schedule__inventory_item",
        )
    )
    if brand_id:
        qs = qs.filter(
            Q(job__brand_id=brand_id) | Q(auto_cut_corte__analysis__brand_id=brand_id),
        )
    if posted_after is not None:
        qs = qs.filter(posted_at__gte=posted_after)
    return qs.order_by("-posted_at", "-id")


def _fetch_post_analytics_cached(
    request_id: str,
    *,
    platform: str = "youtube",
) -> tuple[dict[str, Any] | None, str | None]:
    rid = str(request_id or "").strip()
    if not rid:
        return None, "request_id vazio"

    cache_key = _post_analytics_cache_key(rid, platform=platform)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached.get("data"), cached.get("error")

    data, err = fetch_post_analytics(rid, platform=platform)
    ttl = POST_ANALYTICS_ERROR_CACHE_TTL_SECONDS if err else POST_ANALYTICS_CACHE_TTL_SECONDS
    cache.set(cache_key, {"data": data, "error": err}, ttl)
    return data, err


def _post_has_youtube_platform(post: ScheduledPost) -> bool:
    plats = post.platforms or []
    if not isinstance(plats, list):
        return False
    return bool(set(plats) & {"YT", "YTB"})


def _collect_one_brand(
    brand: Brand,
    *,
    period: str,
) -> dict[str, Any]:
    """Retorna linha agregada + dados brutos mínimos para fusão global."""
    uname = upload_post_profile_username(brand.id)
    line: dict[str, Any] = {
        "brand_id": brand.id,
        "brand_name": brand.name,
        "upload_post_profile": uname,
        "error": None,
        "subscribers": None,
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "videos_published": None,
        "per_day_views": {},
        "youtube_profile_block": None,
        "period_metrics_fallback": False,
    }

    try:
        prof, err_prof = fetch_profile_platforms_analytics(uname, platforms="youtube")
        if err_prof:
            line["error"] = err_prof
        else:
            yt_block = (prof or {}).get("youtube") if isinstance(prof, dict) else None
            if isinstance(yt_block, dict):
                line["youtube_profile_block"] = yt_block
                sub = yt_block.get("followers")
                if sub is None:
                    sub = yt_block.get("subscribers")
                line["subscribers"] = _opt_int(sub)

        metrics_str = "views,likes,comments,shares,video_count"
        total, err_tot = fetch_total_impressions(
            uname,
            period=period,
            platform="youtube",
            metrics=metrics_str,
            breakdown=True,
        )
        if err_tot:
            if not line["error"]:
                line["error"] = err_tot
            elif line["error"] != err_tot:
                line["error"] = f"{line['error']}; período: {err_tot}"
        elif isinstance(total, dict):
            fallback = bool(total.pop("_upload_post_fallback_no_metrics", False))
            line["period_metrics_fallback"] = fallback

            m = total.get("metrics")
            if isinstance(m, dict) and m:
                line["views"] = _opt_int(m.get("views"))
                line["likes"] = _opt_int(m.get("likes"))
                line["comments"] = _opt_int(m.get("comments"))
                line["shares"] = _opt_int(m.get("shares"))
                line["videos_published"] = _opt_int(m.get("video_count"))
            elif fallback or not (isinstance(m, dict) and m):
                # Fallback sem ``metrics``: agrega único campo total_impressions (views no período)
                line["views"] = _opt_int(total.get("total_impressions"))

            pd = total.get("per_day")
            if isinstance(pd, dict):
                flat: dict[str, int] = {}
                for k, v in pd.items():
                    if isinstance(v, dict):
                        for dk, dv in v.items():
                            flat[str(dk)] = flat.get(str(dk), 0) + _safe_int(dv)
                    else:
                        flat[str(k)] = flat.get(str(k), 0) + _safe_int(v)
                line["per_day_views"] = flat
    except Exception as e:
        logger.warning("[FactoryYouTubeDashboard] brand_id=%s: %s", brand.id, e, exc_info=True)
        line["error"] = str(e)[:500]

    return line


def _build_top_posts(
    factory_id: int,
    *,
    brand_id: int | None = None,
    period: str = "last_month",
    force_refresh: bool = False,
    limit_fetch: int = 40,
    top_n: int = 10,
    engagement_n: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = build_factory_youtube_videos(
        factory_id,
        brand_id=brand_id,
        period=period,
        force_refresh=force_refresh,
    )
    results = payload.get("results") or []
    top_posts = [
        {
            "request_id": row.get("request_id") or f"scheduled-post:{row.get('scheduled_post_id')}",
            "brand_id": row.get("brand_id"),
            "title": row.get("published_title"),
            "posted_at": row.get("published_at"),
            "views": row.get("views"),
            "likes": row.get("likes"),
            "comments": row.get("comments"),
            "shares": row.get("shares"),
            "post_url": row.get("post_url"),
            "engagement_score": row.get("engagement_total"),
            "fetch_error": row.get("fetch_error"),
            "platform_error": row.get("platform_error"),
        }
        for row in results[:limit_fetch]
        if row.get("views") is not None
    ][:top_n]
    top_posts_engagement = sorted(
        [
            {
                "request_id": row.get("request_id") or f"scheduled-post:{row.get('scheduled_post_id')}",
                "brand_id": row.get("brand_id"),
                "title": row.get("published_title"),
                "posted_at": row.get("published_at"),
                "views": row.get("views"),
                "likes": row.get("likes"),
                "comments": row.get("comments"),
                "shares": row.get("shares"),
                "post_url": row.get("post_url"),
                "engagement_score": row.get("engagement_total"),
                "fetch_error": row.get("fetch_error"),
                "platform_error": row.get("platform_error"),
            }
            for row in results
            if row.get("engagement_total") is not None and not row.get("fetch_error")
        ],
        key=lambda x: (x.get("engagement_score") or 0),
        reverse=True,
    )[:engagement_n]
    return top_posts, top_posts_engagement


def build_factory_youtube_videos(
    factory_id: int,
    *,
    brand_id: int | None = None,
    period: str | None = None,
    ordering: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    period_norm = period if period in ALLOWED_PERIODS else "last_month"
    ordering_norm = normalize_video_ordering(ordering)
    cache_key = _videos_cache_key_with_ordering(
        factory_id,
        brand_id=brand_id,
        period=period_norm,
        ordering=ordering_norm,
    )
    if force_refresh:
        cache.delete(cache_key)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    posted_after = _period_window_start(period_norm)
    qs = _scheduled_posts_scope_queryset(
        factory_id,
        brand_id=brand_id,
        posted_after=posted_after,
    )
    rows: list[dict[str, Any]] = []
    upload_post_rows: list[tuple[ScheduledPost, dict[str, Any]]] = []
    has_api_key = bool(get_upload_post_api_key())

    for post in qs:
        if not _post_has_youtube_platform(post):
            continue
        request_id = str((post.external_ids or {}).get("upload_post_request_id") or "").strip() or None
        external_video_id = scheduled_post_external_video_id(post)
        post_url = _youtube_watch_url(external_video_id)
        days_since_post = _days_since_post(post.posted_at)
        analytics_source = "upload_post" if request_id else "youtube_api_no_analytics"
        analytics_status = "pending" if request_id else "unavailable"
        if request_id and not has_api_key:
            analytics_status = "api_key_missing"

        row = {
            "scheduled_post_id": post.id,
            "brand_id": scheduled_post_brand_id(post),
            "brand_name": scheduled_post_brand_name(post),
            "published_title": scheduled_post_published_title(post),
            "original_title": scheduled_post_original_title(post),
            "views": None,
            "likes": None,
            "comments": None,
            "impressions": None,
            "shares": None,
            "engagement_total": None,
            "engagement_rate": None,
            "views_per_day": None,
            "days_since_post": days_since_post,
            "recency_factor": _recency_factor(days_since_post, period_norm),
            "viral_score": None,
            "published_at": post.posted_at.isoformat() if post.posted_at else None,
            "post_url": post_url,
            "analytics_source": analytics_source,
            "analytics_status": analytics_status,
            "request_id": request_id,
            "external_video_id": external_video_id,
            "fetch_error": None,
            "platform_error": None,
            "_sort_published_ts": post.posted_at.timestamp() if post.posted_at else 0,
        }
        rows.append(row)
        if request_id and has_api_key:
            upload_post_rows.append((post, row))

    def _fetch_one(item: tuple[ScheduledPost, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
        _post, row = item
        data, err = _fetch_post_analytics_cached(str(row.get("request_id") or ""), platform="youtube")
        return row, data, err

    if upload_post_rows:
        # Serial: o throttle já é thread-safe, mas o rate limit da Upload Post é agressivo —
        # um worker só reduz risco de burst e mantém o cooldown global efetivo.
        with ThreadPoolExecutor(max_workers=1) as pool:
            futs = [pool.submit(_fetch_one, item) for item in upload_post_rows]
            for fut in as_completed(futs):
                row, data, err = fut.result()
                if err or not data:
                    row["analytics_status"] = "fetch_error"
                    row["fetch_error"] = err
                    continue

                platforms_block = data.get("platforms") if isinstance(data, dict) else None
                if isinstance(platforms_block, dict):
                    plat = platforms_block.get("youtube") or {}
                else:
                    plat = {}
                pm = plat.get("post_metrics") if isinstance(plat, dict) else None
                if isinstance(pm, dict):
                    row["views"] = _opt_int(pm.get("views"))
                    row["likes"] = _opt_int(pm.get("likes"))
                    row["comments"] = _opt_int(pm.get("comments"))
                    row["impressions"] = _opt_int(pm.get("impressions"))
                    row["shares"] = _opt_int(pm.get("shares"))
                plat_err = plat.get("post_metrics_error") if isinstance(plat, dict) else None
                if isinstance(plat, dict):
                    row["post_url"] = _first_non_blank(plat.get("post_url"), row.get("post_url"))
                row["platform_error"] = plat_err

                row["engagement_total"] = _sum_available_ints(
                    row["likes"],
                    row["comments"],
                    row["shares"],
                )
                row["views_per_day"] = _views_per_day(row["views"], row["days_since_post"])
                row["engagement_rate"] = _engagement_rate(row["engagement_total"], row["views"])
                row["viral_score"] = _viral_score(
                    views_per_day=row["views_per_day"],
                    engagement_rate=row["engagement_rate"],
                    recency_factor=row["recency_factor"],
                )

                has_any_metric = any(
                    v is not None
                    for v in (
                        row["views"],
                        row["likes"],
                        row["comments"],
                        row["impressions"],
                        row["shares"],
                    )
                )
                if plat_err and not has_any_metric:
                    row["analytics_status"] = "fetch_error"
                    row["fetch_error"] = plat_err
                else:
                    row["analytics_status"] = "available" if has_any_metric else "no_metrics_returned"

    ordered = _sort_video_rows(rows, ordering=ordering_norm)
    for row in ordered:
        row.pop("_sort_published_ts", None)

    upload_post_available = sum(
        1
        for row in ordered
        if row.get("analytics_source") == "upload_post" and row.get("analytics_status") == "available"
    )
    upload_post_source_count = sum(1 for row in ordered if row.get("analytics_source") == "upload_post")
    native_without_analytics = sum(
        1 for row in ordered if row.get("analytics_source") == "youtube_api_no_analytics"
    )

    payload = {
        "scope": {
            "factory_id": factory_id,
            "brand_id": brand_id,
            "period": period_norm,
            "platform": "youtube",
        },
        "count": len(ordered),
        "results": ordered,
        "meta": {
            "upload_post_analytics_available": upload_post_available,
            "upload_post_source_videos": upload_post_source_count,
            "youtube_api_no_analytics_videos": native_without_analytics,
            "total_videos": len(ordered),
            "ordering": ordering_norm,
            "available_orderings": ["views", "viral_score"],
            "default_ordering": "views",
            "viral_score_description": "Combina views/dia, taxa de engajamento e recencia.",
            "viral_score_weights": VIRAL_SCORE_WEIGHTS,
        },
    }
    cache.set(cache_key, payload, CACHE_TTL_SECONDS)
    return payload


def _empty_dashboard(
    factory_id: int,
    period_norm: str,
    *,
    brand_id: int | None = None,
    config_error: str | None = None,
) -> dict[str, Any]:
    return {
        "scope": {
            "factory_id": factory_id,
            "brand_id": brand_id,
            "period": period_norm,
            "platform": "youtube",
        },
        "summary": {
            "total_views": 0,
            "total_likes": 0,
            "total_comments": 0,
            "total_shares": 0,
            "total_subscribers": 0,
            "videos_published": 0,
            "avg_views_per_video": None,
            "subscriber_growth": None,
        },
        "timeseries": [],
        "brands": [],
        "top_posts": [],
        "top_posts_engagement": [],
        "meta": {
            "source": "upload_post",
            "config_error": config_error,
            "profile_username_pattern": "brand_<brand_id>",
            "has_period_metrics": False,
            "has_subscriber_data": False,
        },
    }


def build_factory_youtube_dashboard(
    factory_id: int,
    *,
    brand_id: int | None = None,
    period: str | None = None,
    include_top_posts: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    period_norm = period if period in ALLOWED_PERIODS else "last_month"
    if not get_upload_post_api_key():
        return _empty_dashboard(
            factory_id,
            period_norm,
            brand_id=brand_id,
            config_error="UPLOAD_POST_API_KEY não configurada no servidor.",
        )

    cache_key = _dashboard_cache_key(
        factory_id,
        brand_id=brand_id,
        period=period_norm,
        include_top_posts=include_top_posts,
    )
    if force_refresh:
        cache.delete(cache_key)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    brands_qs = Brand.objects.filter(factory_id=factory_id).order_by("name")
    if brand_id:
        brands_qs = brands_qs.filter(id=brand_id)
    brands = list(brands_qs)
    brand_rows: list[dict[str, Any]] = []
    per_day_maps: list[dict[str, int]] = []
    profile_blocks: list[dict[str, Any]] = []

    sum_views = 0
    sum_likes = 0
    sum_comments = 0
    sum_shares = 0
    sum_subs = 0
    sum_videos = 0

    for i, b in enumerate(brands):
        if i > 0 and _BRAND_EXTRA_DELAY_SEC > 0:
            time.sleep(_BRAND_EXTRA_DELAY_SEC)
        row = _collect_one_brand(b, period=period_norm)
        # cópia segura para resposta (sem objeto ORM)
        clean = {k: v for k, v in row.items() if k != "youtube_profile_block"}
        if row.get("youtube_profile_block"):
            profile_blocks.append(row["youtube_profile_block"])
        if row.get("per_day_views"):
            per_day_maps.append(row["per_day_views"])
        if row["views"] is not None:
            sum_views += row["views"]
        if row["likes"] is not None:
            sum_likes += row["likes"]
        if row["comments"] is not None:
            sum_comments += row["comments"]
        if row["shares"] is not None:
            sum_shares += row["shares"]
        if row["subscribers"] is not None:
            sum_subs += row["subscribers"]
        if row["videos_published"] is not None:
            sum_videos += row["videos_published"]
        brand_rows.append(clean)

    merged_per_day = _merge_per_day_maps(per_day_maps)
    timeseries = [{"date": d, "views": merged_per_day[d]} for d in sorted(merged_per_day.keys())]
    if not timeseries:
        timeseries = _merge_timeseries_from_profiles(profile_blocks)

    top_posts: list[dict[str, Any]] = []
    top_posts_engagement: list[dict[str, Any]] = []
    if include_top_posts:
        try:
            top_posts, top_posts_engagement = _build_top_posts(
                factory_id,
                brand_id=brand_id,
                period=period_norm,
                force_refresh=force_refresh,
            )
        except Exception:
            logger.exception(
                "[FactoryYouTubeDashboard] top_posts factory_id=%s brand_id=%s",
                factory_id,
                brand_id,
            )

    has_period_metrics = any(br.get("views") is not None for br in brand_rows)
    has_subscriber_data = any(br.get("subscribers") is not None for br in brand_rows)

    date_range_note = None
    if brand_rows and any(br.get("views") is not None for br in brand_rows):
        date_range_note = (
            f"Métricas de período via Upload Post (total-impressions), período: {period_norm}. "
            "Assinantes: snapshot atual por canal (profile analytics)."
        )

    out: dict[str, Any] = {
        "scope": {
            "factory_id": factory_id,
            "brand_id": brand_id,
            "period": period_norm,
            "platform": "youtube",
        },
        "summary": {
            "total_views": sum_views,
            "total_likes": sum_likes,
            "total_comments": sum_comments,
            "total_shares": sum_shares,
            "total_subscribers": sum_subs,
            "videos_published": sum_videos,
            "avg_views_per_video": round(sum_views / sum_videos, 2) if sum_videos and sum_views else None,
            "subscriber_growth": None,
        },
        "timeseries": timeseries,
        "brands": brand_rows,
        "top_posts": top_posts,
        "top_posts_engagement": top_posts_engagement,
        "meta": {
            "source": "upload_post",
            "profile_username_pattern": "brand_<brand_id>",
            "date_range_note": date_range_note,
            "has_period_metrics": has_period_metrics,
            "has_subscriber_data": has_subscriber_data,
            "subscriber_growth_unavailable": True,
            "timeseries_note": (
                "Série diária: soma dos per_day do Upload Post por canal; "
                "se indisponível, usa reach_timeseries (~30 dias) somada entre canais."
            ),
        },
    }

    cache.set(cache_key, out, CACHE_TTL_SECONDS)
    return out


def normalize_period_param(raw: str | None) -> str:
    if raw and raw.strip() in ALLOWED_PERIODS:
        return raw.strip()
    return "last_month"
