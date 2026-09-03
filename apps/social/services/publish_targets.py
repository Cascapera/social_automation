"""Resolução de destino de publicação (refactor.md R-08 / D-01).

Movido de `apps/social/tasks.py` (linhas 114-303) — **movimentação pura**: nenhuma linha
de lógica foi editada. A única alteração são 3 imports de `BrandSocialAccount` dentro de
função que saíram, porque o nome agora está no topo do módulo (mesma direção do R-19 d).

A pergunta que este módulo responde é "para onde vai este post": qual plataforma, qual
brand, qual conta social, qual credencial do YouTube — e, quando o vídeo já deveria
estar lá, se ele existe mesmo no canal.

As duas famílias vieram juntas porque estão acopladas: a verificação de existência usa
`_list_ordered_youtube_credentials` para tentar canal por canal. Separá-las criaria
import entre módulos sem ganho.

Fora do Celery, elas ficam testáveis — era esse o ganho previsto no plano.
"""

import logging

from apps.brands.models import BrandSocialAccount, BrandYouTubeCredential
from apps.jobs.models import (
    ScheduledPost,
)

logger = logging.getLogger(__name__)
YOUTUBE_PLATFORM_CODES = {"YT", "YTB"}


def _platforms_are_youtube_only(platforms) -> bool:
    codes = {str(code).strip().upper() for code in (platforms or []) if str(code).strip()}
    return bool(codes) and codes.issubset(YOUTUBE_PLATFORM_CODES)


def _first_youtube_platform(platforms) -> str | None:
    for code in (platforms or []):
        normalized = str(code).strip().upper()
        if normalized in YOUTUBE_PLATFORM_CODES:
            return normalized
    return None


def _youtube_channel_key_and_interval(post) -> tuple[str | None, int]:
    """
    For YouTube posts, returns (channel_key, min_interval_seconds) for serialization.
    channel_key identifies the channel; min_interval is minimum spacing in seconds.
    For non-YouTube returns (None, 0).
    """
    platform = _first_youtube_platform(post.platforms or [])
    if not platform:
        return None, 0
    brand = _resolve_post_target_brand(post) or (getattr(post, "job", None) and getattr(post.job, "brand", None))
    if not brand:
        return None, 0
    account = _resolve_social_account_for_platform(post, brand, platform)
    if not account:
        account = (
            BrandSocialAccount.objects.filter(
                brand=brand,
                platform__in=["YT", "YTB"],
            )
            .order_by("id")
            .first()
        )
    channel_id = (getattr(account, "channel_id", None) or "").strip() if account else ""
    channel_key = f"yt_{channel_id}" if channel_id else f"yt_brand_{brand.id}_{platform}"
    # Defaults: shorts 60 min, long-form 180 min (fixed slots already space; interval is for send queue)
    minutes = 60 if platform == "YT" else 180
    return channel_key, minutes * 60


def _resolve_social_account_for_platform(post: ScheduledPost, brand, platform: str):
    if post.social_account and post.social_account.platform in (platform, "YT", "YTB"):
        return post.social_account

    candidates = [platform]
    if platform == "YT":
        candidates.append("YTB")
    elif platform == "YTB":
        candidates.append("YT")
    return (
        BrandSocialAccount.objects.filter(brand=brand, platform__in=candidates)
        .order_by("id")
        .first()
    )


def _resolve_post_target_brand(post: ScheduledPost):
    """
    Resolve the effective publishing brand.
    Prefers FactoryPostingSchedule brand (factory routing destination).
    """
    try:
        schedule = getattr(post, "factory_schedule", None)
    except Exception:
        schedule = None
    if schedule and getattr(schedule, "brand_id", None):
        return schedule.brand
    if post.job_id:
        return post.job.brand
    if post.auto_cut_corte_id:
        corte = post.auto_cut_corte
        return corte.analysis.brand if corte and corte.analysis_id else None
    return None


def _list_ordered_youtube_credentials(brand):
    if not brand:
        return []
    return list(
        BrandYouTubeCredential.objects.filter(brand=brand, is_active=True)
        .order_by("order_index", "id")
    )


def _source_media_exists(post: ScheduledPost) -> bool:
    try:
        if post.job_id:
            output = getattr(post.job, "output", None)
            return bool(output and output.file and output.file.name)
        if post.auto_cut_corte_id:
            corte = post.auto_cut_corte
            return bool(corte and corte.file and corte.file.name)
    except Exception:
        return False
    return False


def _youtube_video_exists_on_channel(account, video_id: str, youtube_credential=None) -> tuple[bool, dict]:
    """
    Check whether the video exists on the authenticated channel.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    from apps.social.services.youtube_credentials import get_credentials

    # Use same OAuth client that issued the token (brand/global). Check client causes unauthorized_client.
    creds = get_credentials(
        account,
        youtube_credential=youtube_credential,
        use_check_client=False,
    )
    youtube = build("youtube", "v3", credentials=creds)
    try:
        resp = youtube.videos().list(part="id,snippet,status", id=video_id).execute()
    except HttpError as e:
        status_code = getattr(getattr(e, "resp", None), "status", None)
        return False, {"error": f"youtube_api_http_{status_code or 'unknown'}"}
    except Exception as e:
        return False, {"error": f"youtube_api_error:{e}"}
    items = (resp or {}).get("items") or []
    if not items:
        return False, {"error": "video_not_found"}
    item = items[0]
    channel_id = str((item.get("snippet") or {}).get("channelId") or "")
    expected_channel = str(getattr(account, "channel_id", "") or "")
    if expected_channel and channel_id and expected_channel != channel_id:
        return False, {"error": "channel_mismatch", "channel_id": channel_id}
    return True, {
        "channel_id": channel_id,
        "privacy_status": (item.get("status") or {}).get("privacyStatus"),
        "publish_at": (item.get("status") or {}).get("publishAt"),
    }


def _youtube_verify_exists_with_credential_fallback(account, brand, video_id: str) -> tuple[bool, dict]:
    """
    Verify YouTube existence using default account then brand credentials as fallback.
    """
    # 1) try default flow for linked social account
    exists, data = _youtube_video_exists_on_channel(account, video_id)
    if exists:
        return True, data

    # 2) on auth/token error, try brand YouTube credentials
    err = str((data or {}).get("error") or "").lower()
    is_auth_related = any(
        token in err
        for token in ("unauthorized_client", "invalid_grant", "oauth", "token", "credential", "403", "401")
    )
    if not is_auth_related:
        return False, data

    last_data = data or {}
    for yt_cred in _list_ordered_youtube_credentials(brand):
        if not (str(getattr(yt_cred, "refresh_token", "") or "").strip()):
            continue
        exists2, data2 = _youtube_video_exists_on_channel(account, video_id, youtube_credential=yt_cred)
        if exists2:
            return True, data2
        last_data = data2 or last_data
    return False, last_data


def _should_remove_missing_by_verify_error(verify_data: dict) -> bool:
    """
    Only remove from schedule when we have evidence of real absence on YouTube.
    Auth/network/temporary errors do NOT remove.
    """
    err = str((verify_data or {}).get("error") or "").strip().lower()
    if not err:
        return False
    return err in {"video_not_found", "channel_mismatch"}


def _resolve_brand_youtube_account(brand):

    return (
        BrandSocialAccount.objects.filter(brand=brand, platform__in=["YTB", "YT"])
        .order_by("id")
        .first()
    )
