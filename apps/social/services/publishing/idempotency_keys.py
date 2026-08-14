"""Chave de idempotência e identidade de destino da publicação (refactor.md R-13 / D-01).

Estes helpers respondem "esta publicação já foi feita?" antes de qualquer upload. A chave é
montada a partir da **identidade do destino** — brand, canal, plataforma lógica — mais o
fingerprint do arquivo. Duas execuções do mesmo vídeo para o mesmo canal geram a mesma
chave, e a segunda encontra o resultado da primeira em vez de subir de novo.

`_logical_upload_post_platform` existe porque `YT` e `YTB` são a mesma plataforma para o
provedor: sem a normalização, o mesmo vídeo poderia ser enviado duas vezes com chaves
diferentes.

Vieram de `apps/social/tasks.py` no R-13 — movimentação pura. Estavam lá porque a função
gigante os usava; agora quem os usa são os módulos de `publishing/`, e a ida para cá é o
que remove os imports adiados que o R-09 ao R-12 precisaram criar.
"""

from __future__ import annotations

import hashlib

from apps.jobs.models import ScheduledPost
from apps.social.services.publish_targets import (
    YOUTUBE_PLATFORM_CODES,
    _resolve_social_account_for_platform,
)

UPLOAD_POST_CLIENT_REQUEST_ID_KEY = "upload_post_client_request_id"
UPLOAD_POST_PROVIDER_BUSY_STATUS_CODES = {499, 504}
UPLOAD_POST_PROVIDER_BUSY_RETRY_COUNT = 1
UPLOAD_POST_RETRY_COUNT = 2  # Max retries for Upload Post
UPLOAD_POST_RETRY_DELAY_SEC = 10  # Seconds between retries
IDEMPOTENCY_IN_PROGRESS_DELAY_SEC = 60
YOUTUBE_QUOTA_MAX_RETRIES = 2


def _upload_post_retry_limit_for_error(error: Exception) -> int:
    status_code = getattr(error, "status_code", None)
    if status_code in UPLOAD_POST_PROVIDER_BUSY_STATUS_CODES:
        return UPLOAD_POST_PROVIDER_BUSY_RETRY_COUNT
    return UPLOAD_POST_RETRY_COUNT


def _build_upload_post_platforms(brand, post) -> list[str]:
    """Return platform list for Upload Post when enabled on brand."""
    platforms: list[str] = []
    post_platforms = post.platforms or []
    is_short = "YT" in post_platforms and "YTB" not in post_platforms
    is_youtube = "YT" in post_platforms or "YTB" in post_platforms

    # Shorts: TikTok, X, Instagram (Reels) + YouTube when enabled
    if is_short:
        if getattr(brand, "upload_post_tiktok_enabled", False):
            platforms.append("TIKTOK")
        if getattr(brand, "upload_post_x_enabled", False):
            platforms.append("X")
        if getattr(brand, "upload_post_instagram_enabled", False):
            platforms.append("INSTAGRAM")
    # Long-form: YouTube only (TikTok/Instagram have duration limits)
    if is_youtube and getattr(brand, "upload_post_youtube_enabled", False):
        platforms.append("YOUTUBE")
    return platforms


def _logical_upload_post_platform(post: ScheduledPost, upload_post_platform: str) -> str:
    normalized = str(upload_post_platform).strip().upper()
    if normalized == "YOUTUBE":
        return "YT" if ("YT" in (post.platforms or []) and "YTB" not in (post.platforms or [])) else "YTB"
    return {
        "TIKTOK": "TT",
        "INSTAGRAM": "IG",
        "X": "X",
    }[normalized]


def _resolve_publish_target_identity(
    post: ScheduledPost,
    brand,
    platform: str,
    *,
    account=None,
) -> str:
    normalized = str(platform).strip().upper()
    resolved_account = account
    if resolved_account is None and normalized in YOUTUBE_PLATFORM_CODES:
        resolved_account = _resolve_social_account_for_platform(post, brand, normalized)
    channel_id = str(getattr(resolved_account, "channel_id", "") or "").strip()
    if channel_id:
        return channel_id
    account_id = getattr(resolved_account, "id", None)
    if account_id:
        return f"social_account_{account_id}"
    return f"brand_{brand.id}_{normalized}"


def _build_publish_idempotency_key(
    post: ScheduledPost,
    brand,
    platform: str,
    upload_fingerprint: str,
    *,
    account=None,
) -> str:
    target_identity = _resolve_publish_target_identity(
        post,
        brand,
        platform,
        account=account,
    )
    return f"publish:{platform}:{target_identity}:{upload_fingerprint}"


def _build_upload_post_provider_keys(
    upload_post_platforms: list[str],
    upload_post_keys_by_platform: dict[str, str],
) -> tuple[str, str]:
    """
    Gera os identificadores estáveis enviados ao Upload Post.

    Reaproveita as chaves de idempotência locais já calculadas para que retries após
    timeout/rede consultem o mesmo job remoto em vez de abrir uma nova postagem.
    """
    normalized_platforms = sorted(
        {
            str(platform).strip().upper()
            for platform in (upload_post_platforms or [])
            if str(platform).strip()
        }
    )
    seed_parts = [
        upload_post_keys_by_platform.get(platform) or platform
        for platform in normalized_platforms
    ]
    seed = "|".join(seed_parts)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"upreq-{digest[:32]}", f"upidem-{digest}"


def _apply_idempotency_result(external_ids: dict, result_payload: dict | None) -> None:
    payload = result_payload or {}
    for key in payload.get("remove_external_ids") or []:
        external_ids.pop(str(key), None)
    for key, value in (payload.get("external_ids") or {}).items():
        if value is None or value == "":
            continue
        external_ids[str(key)] = value


def _build_idempotency_retryable_error(platform: str) -> dict:
    return {
        "message": f"{platform}: publicação já está em andamento para esta chave idempotente",
        "retry_after_seconds": IDEMPOTENCY_IN_PROGRESS_DELAY_SEC,
        "reason": "idempotencyInProgress",
    }


def _upload_post_pending_idempotency_without_provider_ids(result_payload: dict | None) -> bool:
    """
    Unknown/pending Upload Post sem ``request_id``/``job_id`` não é um replay reutilizável para
    um novo ScheduledPost. Esse caso deve voltar ao fluxo normal de envio.
    """
    payload = result_payload or {}
    if not bool(payload.get("upload_post_reconciliation_pending")):
        return False
    ext = (payload.get("external_ids") or {}) if isinstance(payload, dict) else {}
    request_id = str(ext.get("upload_post_request_id") or "").strip()
    job_id = str(ext.get("upload_post_job_id") or "").strip()
    return not request_id and not job_id
