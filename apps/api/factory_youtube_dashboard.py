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
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from django.core.cache import cache
from django.db.models import Q

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
ALLOWED_PERIODS = frozenset({"last_day", "last_week", "last_month", "last_3months", "last_year"})
# Pausa extra entre marcas (além do throttle no cliente HTTP) para não disparar 429 em fábricas grandes.
_BRAND_EXTRA_DELAY_SEC = float(os.getenv("UPLOAD_POST_FACTORY_BRAND_DELAY_SEC", "0.15"))


def upload_post_profile_username(brand_id: int) -> str:
    return f"brand_{int(brand_id)}"


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
    factory_brand_ids: set[int],
    limit_fetch: int = 40,
    top_n: int = 10,
    engagement_n: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    qs = (
        ScheduledPost.objects.filter(status="DONE", posted_at__isnull=False)
        .filter(
            Q(job__brand__factory_id=factory_id) | Q(auto_cut_corte__analysis__brand__factory_id=factory_id),
        )
        .select_related("job__brand", "auto_cut_corte__analysis__brand")
        .order_by("-posted_at")[:limit_fetch]
    )
    candidates: list[ScheduledPost] = []
    for post in qs:
        if not _post_has_youtube_platform(post):
            continue
        ext = post.external_ids or {}
        rid = ext.get("upload_post_request_id")
        if not rid:
            continue
        bid = scheduled_post_brand_id(post)
        if bid is None or bid not in factory_brand_ids:
            continue
        candidates.append(post)

    def _fetch_one(post: ScheduledPost) -> tuple[ScheduledPost, str, dict[str, Any] | None, str | None]:
        rid = (post.external_ids or {}).get("upload_post_request_id")
        if not rid:
            return post, "", None, "request_id ausente"
        data, err = fetch_post_analytics(str(rid), platform="youtube")
        return post, str(rid), data, err

    results: list[dict[str, Any]] = []
    to_fetch = candidates[:limit_fetch]
    if not to_fetch:
        return [], []

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_fetch_one, p) for p in to_fetch]
        for fut in as_completed(futs):
            post, rid, data, err = fut.result()
            if not rid:
                continue
            if err or not data:
                results.append(
                    {
                        "request_id": str(rid) if rid else "",
                        "brand_id": scheduled_post_brand_id(post),
                        "title": (post.title or "").strip() or None,
                        "posted_at": post.posted_at.isoformat() if post.posted_at else None,
                        "views": None,
                        "likes": None,
                        "comments": None,
                        "shares": None,
                        "post_url": None,
                        "engagement_score": None,
                        "fetch_error": err,
                        "platform_error": None,
                    }
                )
                continue
            plat = (data.get("platforms") or {}).get("youtube") or {}
            pm = plat.get("post_metrics") if isinstance(plat, dict) else None
            views = _opt_int((pm or {}).get("views")) if isinstance(pm, dict) else None
            likes = _opt_int((pm or {}).get("likes")) if isinstance(pm, dict) else None
            comments = _opt_int((pm or {}).get("comments")) if isinstance(pm, dict) else None
            shares = _opt_int((pm or {}).get("shares")) if isinstance(pm, dict) else None
            post_url = plat.get("post_url") if isinstance(plat, dict) else None
            if likes is None and comments is None and shares is None:
                eng_score = None
            else:
                eng_score = _safe_int(likes) + _safe_int(comments) + _safe_int(shares)
            plat_err = plat.get("post_metrics_error") if isinstance(plat, dict) else None
            results.append(
                {
                    "request_id": str(rid),
                    "brand_id": scheduled_post_brand_id(post),
                    "title": (post.title or "").strip() or None,
                    "posted_at": post.posted_at.isoformat() if post.posted_at else None,
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "shares": shares,
                    "post_url": post_url,
                    "engagement_score": eng_score,
                    "fetch_error": None,
                    "platform_error": plat_err,
                }
            )

    by_views = sorted(
        [r for r in results if r.get("views") is not None],
        key=lambda x: (x.get("views") or 0),
        reverse=True,
    )[:top_n]
    by_eng = sorted(
        [
            r
            for r in results
            if r.get("engagement_score") is not None and not r.get("fetch_error")
        ],
        key=lambda x: (x.get("engagement_score") or 0),
        reverse=True,
    )[:engagement_n]
    return by_views, by_eng


def _empty_dashboard(factory_id: int, period_norm: str, *, config_error: str | None = None) -> dict[str, Any]:
    return {
        "scope": {"factory_id": factory_id, "period": period_norm, "platform": "youtube"},
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


def build_factory_youtube_dashboard(factory_id: int, *, period: str | None = None) -> dict[str, Any]:
    period_norm = period if period in ALLOWED_PERIODS else "last_month"
    if not get_upload_post_api_key():
        return _empty_dashboard(factory_id, period_norm, config_error="UPLOAD_POST_API_KEY não configurada no servidor.")

    cache_key = f"factory_youtube_dash:{factory_id}:{period_norm}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    brands = list(Brand.objects.filter(factory_id=factory_id).order_by("name"))
    factory_brand_ids = {b.id for b in brands}
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
    try:
        top_posts, top_posts_engagement = _build_top_posts(
            factory_id,
            factory_brand_ids=factory_brand_ids,
        )
    except Exception:
        logger.exception("[FactoryYouTubeDashboard] top_posts factory_id=%s", factory_id)

    has_period_metrics = any(br.get("views") is not None for br in brand_rows)
    has_subscriber_data = any(br.get("subscribers") is not None for br in brand_rows)

    date_range_note = None
    if brand_rows and any(br.get("views") is not None for br in brand_rows):
        date_range_note = (
            f"Métricas de período via Upload Post (total-impressions), período: {period_norm}. "
            "Assinantes: snapshot atual por canal (profile analytics)."
        )

    out: dict[str, Any] = {
        "scope": {"factory_id": factory_id, "period": period_norm, "platform": "youtube"},
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
