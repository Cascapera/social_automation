"""Publicação via Upload-Post (refactor.md R-11 / D-01).

Terceira das quatro fatias de `_run_post_to_platforms`. É o caminho preferencial para
TikTok, X, Instagram e — quando a brand liga — YouTube: um upload só, distribuído pelo
provedor.

O que torna este ramo difícil não é o upload, é o que acontece quando ele **não responde
com clareza**:

  - **idempotência por plataforma lógica** — a chave é adquirida antes do envio, e um
    `succeeded` anterior reaplica o resultado em vez de subir de novo;
  - **resultado UNKNOWN** — o provedor aceitou mas não confirmou. Nesse caso o post fica
    pendente de reconciliação, e o fallback nativo do YouTube é **bloqueado**, senão o
    vídeo sobe duas vezes;
  - **retry com espera** entre tentativas, e fallback para a API nativa só depois de falha
    confirmada.

Três caminhos daqui **encerram a task inteira** (`pending_result` duas vezes e
`replacement_result` uma). Eles saem como `early_return` no resultado — quem chama devolve
esse dict e para. Era `return` direto de dentro do bloco.

⚠ **Nada foi editado no R-11**: chave de idempotência, plataformas enviadas e política de
retry são as mesmas.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.utils import timezone

from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.models import ScheduledPost
from apps.social.services.idempotency import (
    acquire_idempotency_key,
    get_existing_idempotency_result,
    mark_idempotency_failed,
    mark_idempotency_success,
)

logger = logging.getLogger(__name__)

YOUTUBE_PLATFORM_CODES = {"YT", "YTB"}


@dataclass
class UploadPostResult:
    """`external_ids` não vem aqui: é mutado no lugar, como no código original."""

    early_return: dict | None = None
    upload_post_youtube_ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retryable_errors: list[dict] = field(default_factory=list)


def publish_via_upload_post(
    post: ScheduledPost,
    brand: Any,
    *,
    video_path: str,
    upload_post_platforms: list[str],
    upload_fingerprint: str,
    external_ids: dict,
    correlation_id: str,
    brand_id: int | None,
    current_attempt: int,
    timer: Timer,
    upload_post_youtube_ok: bool,
) -> UploadPostResult:
    """Envia ao Upload-Post e trata retry, idempotência e resultado indefinido."""
    # Import adiado: estes helpers ainda moram em `tasks.py`. Saem no R-12/R-13.
    from apps.social.tasks import (
        UPLOAD_POST_CLIENT_REQUEST_ID_KEY,
        UPLOAD_POST_PROVIDER_BUSY_STATUS_CODES,
        UPLOAD_POST_RETRY_COUNT,
        UPLOAD_POST_RETRY_DELAY_SEC,
        _apply_idempotency_result,
        _build_idempotency_retryable_error,
        _build_publish_idempotency_key,
        _build_upload_post_provider_keys,
        _logical_upload_post_platform,
        _replace_ambiguous_short_slot,
        _schedule_upload_post_unknown_reconciliation,
        _upload_post_pending_idempotency_without_provider_ids,
        _upload_post_retry_limit_for_error,
    )

    resultado = UploadPostResult(upload_post_youtube_ok=upload_post_youtube_ok)
    warnings = resultado.warnings
    retryable_errors = resultado.retryable_errors
    _brand_id = brand_id
    _timer = timer

    from apps.social.publishers.upload_post import (
        UploadPostErrorKind,
        UploadPostPublishError,
        publish_to_upload_post,
    )

    title = (post.title or "").strip() or "Vídeo"
    desc_by_platform = {}
    for p in upload_post_platforms:
        extra = ""
        if p == "TIKTOK":
            extra = (getattr(brand, "upload_post_tiktok_extra_description", "") or "").strip()
        elif p == "X":
            extra = (getattr(brand, "upload_post_x_extra_description", "") or "").strip()
        elif p == "INSTAGRAM":
            extra = (getattr(brand, "upload_post_instagram_extra_description", "") or "").strip()
        elif p == "YOUTUBE":
            extra = (getattr(brand, "youtube_description_extra", "") or "").strip()
        desc_by_platform[p] = f"{title}\n\n{extra}".strip() if extra else title
    tz_name = "America/Sao_Paulo"
    if getattr(brand, "factory_id", None) and getattr(brand, "factory", None):
        tz_name = (brand.factory.timezone or "").strip() or tz_name

    upload_post_keys_by_platform: dict[str, str] = {}
    upload_post_platforms_to_execute: list[str] = []
    for up_platform in upload_post_platforms:
        logical_platform = _logical_upload_post_platform(post, up_platform)
        idempotency_key = _build_publish_idempotency_key(
            post,
            brand,
            logical_platform,
            upload_fingerprint,
        )
        acquire_result = acquire_idempotency_key(
            key=idempotency_key,
            operation_name="publish",
            aggregate_type="ScheduledPost",
            aggregate_id=post.id,
        )
        if acquire_result.outcome == "succeeded":
            existing_payload = get_existing_idempotency_result(idempotency_key) or acquire_result.record.result_payload
            if _upload_post_pending_idempotency_without_provider_ids(existing_payload):
                logger.warning(
                    "[UploadPost] Ignoring stale pending idempotency replay without provider ids "
                    "(post_id=%s logical_platform=%s)",
                    post.id,
                    logical_platform,
                )
                mark_idempotency_failed(
                    key=idempotency_key,
                    error_message=(
                        "Upload Post pending replay sem request_id/job_id não é reutilizável "
                        "para um novo ScheduledPost"
                    ),
                    result_payload=existing_payload,
                )
                reacquire_result = acquire_idempotency_key(
                    key=idempotency_key,
                    operation_name="publish",
                    aggregate_type="ScheduledPost",
                    aggregate_id=post.id,
                )
                if reacquire_result.outcome == "in_progress":
                    retryable_errors.append(_build_idempotency_retryable_error(logical_platform))
                    continue
                if reacquire_result.outcome == "succeeded":
                    existing_payload = (
                        get_existing_idempotency_result(idempotency_key) or reacquire_result.record.result_payload
                    )
                    _apply_idempotency_result(external_ids, existing_payload)
                    existing_external_ids = (existing_payload or {}).get("external_ids") or {}
                    if logical_platform in YOUTUBE_PLATFORM_CODES and (
                        existing_external_ids.get("youtube_via_upload_post")
                        or existing_external_ids.get(logical_platform)
                    ):
                        upload_post_youtube_ok = True
                    continue
                upload_post_keys_by_platform[up_platform] = idempotency_key
                upload_post_platforms_to_execute.append(up_platform)
                continue
            _apply_idempotency_result(external_ids, existing_payload)
            existing_external_ids = (existing_payload or {}).get("external_ids") or {}
            if logical_platform in YOUTUBE_PLATFORM_CODES and (
                existing_external_ids.get("youtube_via_upload_post") or existing_external_ids.get(logical_platform)
            ):
                upload_post_youtube_ok = True
            continue
        if acquire_result.outcome == "in_progress":
            retryable_errors.append(_build_idempotency_retryable_error(logical_platform))
            continue
        upload_post_keys_by_platform[up_platform] = idempotency_key
        upload_post_platforms_to_execute.append(up_platform)

    up_success = False
    last_up_error = None
    if upload_post_platforms_to_execute:
        desc_by_platform = {
            key: value
            for key, value in desc_by_platform.items()
            if key in upload_post_platforms_to_execute
        }
        upload_post_request_id, upload_post_provider_idempotency_key = _build_upload_post_provider_keys(
            upload_post_platforms_to_execute,
            upload_post_keys_by_platform,
        )
        upload_post_result_keys = {
            "TIKTOK": "tiktok",
            "X": "x",
            "INSTAGRAM": "instagram",
            "YOUTUBE": "youtube",
        }
        # Capa customizada via Upload-Post: somente para vídeos longos (YTB).
        # Upload-Post ignora thumbnail em Shorts (YT). Sem corte ou sem arquivo, segue sem capa.
        upload_post_thumbnail_path: str | None = None
        if (
            "YOUTUBE" in upload_post_platforms_to_execute
            and "YTB" in (post.platforms or [])
            and getattr(post, "auto_cut_corte_id", None)
        ):
            corte_thumb = getattr(getattr(post, "auto_cut_corte", None), "thumbnail", None)
            if corte_thumb:
                try:
                    candidate = Path(corte_thumb.path)
                    if candidate.exists():
                        upload_post_thumbnail_path = str(candidate)
                except (ValueError, OSError) as e:
                    logger.warning(
                        "[UploadPost] Não foi possível resolver thumbnail do corte para post %s: %s",
                        post.id,
                        e,
                    )
        for attempt in range(UPLOAD_POST_RETRY_COUNT + 1):
            try:
                result = publish_to_upload_post(
                    video_path=video_path,
                    brand_id=brand.id,
                    platforms=upload_post_platforms_to_execute,
                    title=title,
                    description_by_platform=desc_by_platform,
                    scheduled_at=post.scheduled_at,
                    timezone_name=tz_name,
                    request_id=upload_post_request_id,
                    idempotency_key=upload_post_provider_idempotency_key,
                    thumbnail_path=upload_post_thumbnail_path,
                )
                if result.get("success"):
                    for key in (
                        "upload_post_reconciliation_state",
                        "upload_post_no_provider_id_check_count",
                        "upload_post_resend_count",
                        "upload_post_youtube_terminal_failure",
                    ):
                        external_ids.pop(key, None)
                    external_ids.pop(UPLOAD_POST_CLIENT_REQUEST_ID_KEY, None)
                    provider_request_id = str(result.get("provider_request_id") or "").strip()
                    client_request_id = str(
                        result.get(UPLOAD_POST_CLIENT_REQUEST_ID_KEY) or result.get("client_request_id") or ""
                    ).strip()
                    if not client_request_id:
                        client_request_id = upload_post_request_id
                    request_id = provider_request_id or client_request_id
                    job_id_out = str(result.get("job_id") or "").strip()
                    request_id_source = str(result.get("request_id_source") or "").strip()
                    logger.info(
                        "[UploadPost] Posting confirmation received (id %s)",
                        request_id or "ok",
                    )
                    if provider_request_id:
                        external_ids["upload_post_request_id"] = provider_request_id
                    if job_id_out:
                        external_ids["upload_post_job_id"] = job_id_out
                    external_ids["upload_post_last_status"] = "submitted"
                    external_ids["upload_post_last_checked_at"] = timezone.now().isoformat()
                    up_results = (result.get("data") or {}).get("results") or {}
                    youtube_provider_reference = bool(provider_request_id or job_id_out)
                    youtube_platform_result_id = False
                    for up_platform in upload_post_platforms_to_execute:
                        logical_platform = _logical_upload_post_platform(post, up_platform)
                        plat_data = up_results.get(upload_post_result_keys[up_platform]) or {}
                        external_ids_delta: dict[str, str | bool] = {}
                        if provider_request_id:
                            external_ids_delta["upload_post_request_id"] = provider_request_id
                        if job_id_out:
                            external_ids_delta["upload_post_job_id"] = job_id_out
                        if plat_data.get("success"):
                            vid = plat_data.get("video_id") or plat_data.get("publish_id")
                            if vid:
                                external_ids[logical_platform] = str(vid)
                                external_ids_delta[logical_platform] = str(vid)
                        if logical_platform in YOUTUBE_PLATFORM_CODES and external_ids_delta.get(logical_platform):
                            youtube_platform_result_id = True
                        if logical_platform in YOUTUBE_PLATFORM_CODES and (
                            youtube_provider_reference or external_ids_delta.get(logical_platform)
                        ):
                            upload_post_youtube_ok = True
                            external_ids["youtube_via_upload_post"] = True
                            external_ids_delta["youtube_via_upload_post"] = True
                    if (
                        "YOUTUBE" in upload_post_platforms_to_execute
                        and not youtube_provider_reference
                        and not youtube_platform_result_id
                    ):
                        logger.warning(
                            "[UploadPost] Success without provider request_id/job_id; "
                            "holding post for controlled reconciliation (post_id=%s request_id_source=%s)",
                            post.id,
                            request_id_source or "unknown",
                        )
                        pending_result = _schedule_upload_post_unknown_reconciliation(
                            post,
                            brand=brand,
                            correlation_id=correlation_id,
                            brand_id=_brand_id,
                            current_attempt=current_attempt,
                            _timer=_timer,
                            upload_fingerprint=upload_fingerprint,
                            external_ids=external_ids,
                            upload_post_keys_by_platform=upload_post_keys_by_platform,
                            provider_request_id=provider_request_id or None,
                            client_request_id=client_request_id or None,
                            job_id=job_id_out or None,
                            last_status="accepted_without_provider_ids",
                            status_code=None,
                            detail="Upload Post confirmou o envio sem request_id/job_id rastreável do provedor.",
                        )
                        if pending_result is not None:
                            resultado.early_return = pending_result
                            return resultado
                    up_success = True
                    for up_platform in upload_post_platforms_to_execute:
                        logical_platform = _logical_upload_post_platform(post, up_platform)
                        plat_data = up_results.get(upload_post_result_keys[up_platform]) or {}
                        external_ids_delta: dict[str, str | bool] = {}
                        if provider_request_id:
                            external_ids_delta["upload_post_request_id"] = provider_request_id
                        if job_id_out:
                            external_ids_delta["upload_post_job_id"] = job_id_out
                        if external_ids.get(logical_platform):
                            external_ids_delta[logical_platform] = str(external_ids[logical_platform])
                        if logical_platform in YOUTUBE_PLATFORM_CODES and (
                            youtube_provider_reference or external_ids_delta.get(logical_platform)
                        ):
                            external_ids_delta["youtube_via_upload_post"] = True
                        mark_idempotency_success(
                            key=upload_post_keys_by_platform[up_platform],
                            result_payload={
                                "platform": logical_platform,
                                "publisher": "upload_post",
                                "external_ids": external_ids_delta,
                                "provider_response": plat_data,
                                "request_id": request_id,
                            },
                        )
                    break
                last_up_error = str(result.get("error") or "error")
            except UploadPostPublishError as e:
                last_up_error = str(e)
                if e.kind == UploadPostErrorKind.UNKNOWN_PENDING_CONFIRMATION:
                    provider_request_id = None
                    client_request_id = None
                    if e.request_id_source == "provider":
                        provider_request_id = e.request_id or external_ids.get("upload_post_request_id")
                    else:
                        client_request_id = e.request_id or external_ids.get(UPLOAD_POST_CLIENT_REQUEST_ID_KEY)
                        if not client_request_id:
                            client_request_id = upload_post_request_id
                    pending_result = _schedule_upload_post_unknown_reconciliation(
                        post,
                        brand=brand,
                        correlation_id=correlation_id,
                        brand_id=_brand_id,
                        current_attempt=current_attempt,
                        _timer=_timer,
                        upload_fingerprint=upload_fingerprint,
                        external_ids=external_ids,
                        upload_post_keys_by_platform=upload_post_keys_by_platform,
                        provider_request_id=provider_request_id,
                        client_request_id=client_request_id,
                        job_id=e.job_id or external_ids.get("upload_post_job_id"),
                        status_code=e.status_code,
                        last_status=f"unknown_http_{e.status_code or 'na'}",
                        detail=last_up_error,
                    )
                    if pending_result is not None:
                        resultado.early_return = pending_result
                        return resultado
                retry_limit = _upload_post_retry_limit_for_error(e)
                if attempt < retry_limit and e.retriable:
                    logger.warning(
                        "[UploadPost] Error (attempt %s/%s), retry in %s seconds: %s",
                        attempt + 1,
                        retry_limit + 1,
                        UPLOAD_POST_RETRY_DELAY_SEC,
                        last_up_error,
                    )
                    _time.sleep(UPLOAD_POST_RETRY_DELAY_SEC)
                else:
                    if e.status_code in UPLOAD_POST_PROVIDER_BUSY_STATUS_CODES:
                        replacement_result = _replace_ambiguous_short_slot(
                            post,
                            current_attempt=current_attempt,
                            correlation_id=correlation_id,
                            duration_ms=_timer.elapsed_ms(),
                            provider_not_found=False,
                            detail=last_up_error,
                            external_ids=external_ids,
                            keep_failed_inventory_available=True,
                            reason_prefix_override=(
                                "Upload Post: erro temporário do provedor após tentar novamente o mesmo vídeo."
                            ),
                        )
                        if replacement_result is not None:
                            for idempotency_key in upload_post_keys_by_platform.values():
                                mark_idempotency_failed(key=idempotency_key, error_message=last_up_error)
                            resultado.early_return = replacement_result
                            return resultado
                    logger.warning(
                        "[UploadPost] Failed after %s attempts: %s",
                        retry_limit + 1,
                        last_up_error,
                    )
                    if "YOUTUBE" in upload_post_platforms_to_execute:
                        log_event(
                            logger,
                            event="upload_post_fallback_allowed",
                            correlation_id=correlation_id,
                            scheduled_post_id=post.id,
                            reason="confirmed_upload_post_failure",
                        )
                        logger.info("[UploadPost] Falling back to YouTube API (confirmed failure)")
            except Exception as e:
                last_up_error = str(e)
                if attempt < UPLOAD_POST_RETRY_COUNT:
                    logger.warning(
                        "[UploadPost] Error (attempt %s/%s), retry in %s seconds: %s",
                        attempt + 1,
                        UPLOAD_POST_RETRY_COUNT + 1,
                        UPLOAD_POST_RETRY_DELAY_SEC,
                        last_up_error,
                    )
                    _time.sleep(UPLOAD_POST_RETRY_DELAY_SEC)
                else:
                    logger.warning(
                        "[UploadPost] Failed after %s attempts: %s",
                        UPLOAD_POST_RETRY_COUNT + 1,
                        last_up_error,
                    )
                    if "YOUTUBE" in upload_post_platforms_to_execute:
                        log_event(
                            logger,
                            event="upload_post_fallback_allowed",
                            correlation_id=correlation_id,
                            scheduled_post_id=post.id,
                            reason="confirmed_upload_post_exception",
                        )
                        logger.info("[UploadPost] Falling back to YouTube API (confirmed failure)")

    if not up_success and last_up_error:
        for idempotency_key in upload_post_keys_by_platform.values():
            mark_idempotency_failed(key=idempotency_key, error_message=last_up_error)
        if "YOUTUBE" not in upload_post_platforms_to_execute:
            warnings.append(f"Upload-Post: {last_up_error}")

    resultado.upload_post_youtube_ok = upload_post_youtube_ok
    return resultado
