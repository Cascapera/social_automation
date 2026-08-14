"""Publicação nativa no YouTube (refactor.md R-10 / D-01).

Segunda das quatro fatias de `_run_post_to_platforms`, e a mais delicada: é o caminho que
sobe o vídeo de fato. O plano classifica este item como **risco médio-alto — caminho
crítico de receita do produto**.

O laço percorre as plataformas do post e, para cada uma, decide entre publicar, pular ou
falhar. As guardas, na ordem:

  1. YouTube já publicado via Upload-Post → pula (evita Short duplicado);
  2. conta social resolvida — `YT` e `YTB` compartilham o mesmo OAuth;
  3. reconciliação do Upload-Post pendente → adia o fallback nativo;
  4. `invalid_grant` já registrado → falha sem tentar de novo;
  5. **chave de idempotência** — `succeeded` reaplica o resultado, `in_progress` adia;
  6. deduplicação por fingerprint de arquivo + canal;
  7. cota: credenciais sem quota são puladas, e se **todas** estiverem sem, o post é adiado
     até o reset.

Com credenciais de brand, cada uma é tentada em ordem e o erro fica gravado nela — é o que
permite a próxima tentativa começar da credencial certa.

⚠ **Nada aqui foi editado no R-10.** O corpo é o mesmo, incluindo um bloco inalcançável
depois de um `continue` incondicional (registrado como achado, sai num `fix()` próprio,
como o projeto fez no R-22). Payload enviado ao YouTube, `external_ids` gravados, eventos
de log e métricas: idênticos.

`external_ids` é recebido e **mutado no lugar**, de propósito: é o mesmo dicionário que o
ramo do Upload-Post já preencheu antes, e copiar mudaria o comportamento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
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
from apps.social.services.publish_targets import _list_ordered_youtube_credentials

logger = logging.getLogger(__name__)

YOUTUBE_PLATFORM_CODES = {"YT", "YTB"}


@dataclass
class NativePublishResult:
    """O que o laço produziu. `external_ids` não vem aqui: foi mutado no lugar."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retryable_errors: list[dict] = field(default_factory=list)
    social_account_changed: bool = False


def publish_native_platforms(
    post: ScheduledPost,
    brand: Any,
    *,
    video_path: str,
    job: Any,
    correlation_id: str,
    brand_id: int | None,
    upload_fingerprint: str,
    upload_post_youtube_ok: bool,
    external_ids: dict,
    upload_post_client_request_id_key: str,
) -> NativePublishResult:
    """Publica nas plataformas nativas do post. Não levanta: erro vira item da lista."""
    # Import adiado: os 3 helpers de idempotência ainda moram em `tasks.py` e são usados
    # também pelo ramo do Upload-Post. Saem no R-11, e aí o import sobe para o topo.
    from apps.social.tasks import (
        _apply_idempotency_result,
        _build_idempotency_retryable_error,
        _build_publish_idempotency_key,
    )

    resultado = NativePublishResult()
    errors = resultado.errors
    warnings = resultado.warnings
    retryable_errors = resultado.retryable_errors
    social_account_changed = False
    _brand_id = brand_id
    job_obj = job
    UPLOAD_POST_CLIENT_REQUEST_ID_KEY = upload_post_client_request_id_key

    for platform in post.platforms:
        # If YouTube was already handled via Upload Post, skip native API (avoid duplicate Short).
        # upload_post_youtube_ok is True with async (request_id only, no video_id yet) — do not require external_ids.
        if platform in ("YT", "YTB") and upload_post_youtube_ok:
            logger.info(
                "[POSTING] YouTube via Upload Post already applied; skipping publisher API (post_id=%s platform=%s)",
                post.id,
                platform,
            )
            continue
        account = post.social_account
        if not account or account.platform != platform:
            from apps.brands.models import BrandSocialAccount

            # YouTube Shorts (YT) and long-form (YTB) share the same OAuth.
            # If no account for exact code, try alternate code.
            platform_candidates = [platform]
            if platform == "YT":
                platform_candidates.append("YTB")
            elif platform == "YTB":
                platform_candidates.append("YT")

            account = (
                BrandSocialAccount.objects.filter(
                    brand=brand,
                    platform__in=platform_candidates,
                )
                .order_by("id")
                .first()
            )
        if not account:
            if str(platform).strip().upper() in YOUTUBE_PLATFORM_CODES and _list_ordered_youtube_credentials(brand):
                account = SimpleNamespace(
                    brand=brand,
                    platform=platform,
                    channel_id="",
                    access_token="",
                    refresh_token="",
                )
            else:
                errors.append(f"{platform}: nenhuma conta conectada")
                continue
        if str(platform).strip().upper() in YOUTUBE_PLATFORM_CODES and external_ids.get(
            "upload_post_reconciliation_state"
        ) == "pending":
            log_event(
                logger,
                event="upload_post_fallback_blocked",
                correlation_id=correlation_id,
                scheduled_post_id=post.id,
                reason="reconciliation_pending_native_skipped",
            )
            retryable_errors.append(
                {
                    "message": f"{platform}: aguardando confirmação do Upload Post antes do fallback nativo",
                    "retry_after_seconds": 120,
                    "reason": "uploadPostReconciliationPending",
                }
            )
            continue
        if str(platform).strip().upper() in YOUTUBE_PLATFORM_CODES and external_ids.get("youtube_native_invalid_grant"):
            msg_ig = "YouTube OAuth inválido (invalid_grant); atualize a credencial."
            errors.append(f"{platform}: {msg_ig}")
            log_event(
                logger,
                event="youtube_native_invalid_grant",
                correlation_id=correlation_id,
                scheduled_post_id=post.id,
                brand_id=_brand_id,
                platform="youtube",
                status="credential_failure",
            )
            continue
        # SimpleNamespace is only for publishing with BrandYouTubeCredential; FK requires BrandSocialAccount.
        if not post.social_account_id and isinstance(account, BrandSocialAccount):
            post.social_account = account
            social_account_changed = True
        idempotency_key = _build_publish_idempotency_key(
            post,
            brand,
            platform,
            upload_fingerprint,
            account=account,
        )
        acquire_result = acquire_idempotency_key(
            key=idempotency_key,
            operation_name="publish",
            aggregate_type="ScheduledPost",
            aggregate_id=post.id,
        )
        if acquire_result.outcome == "succeeded":
            existing_payload = get_existing_idempotency_result(idempotency_key) or acquire_result.record.result_payload
            _apply_idempotency_result(external_ids, existing_payload)
            continue
        if acquire_result.outcome == "in_progress":
            retryable_errors.append(_build_idempotency_retryable_error(platform))
            continue
        # Extra deduplication: avoid accidental duplicate upload for same channel/platform.
        if upload_fingerprint and platform in ("YT", "YTB"):
            done_posts = ScheduledPost.objects.filter(
                status="DONE",
                upload_fingerprint=upload_fingerprint,
            ).exclude(id=post.id).select_related("social_account")
            duplicated = False
            for done_post in done_posts:
                done_platforms = done_post.platforms or []
                same_platform = platform in done_platforms
                same_channel = (
                    done_post.social_account_id
                    and account.channel_id
                    and done_post.social_account.channel_id == account.channel_id
                )
                if same_platform and same_channel:
                    duplicated = True
                    break
            if duplicated:
                duplicate_message = f"{platform}: upload duplicado detectado (mesmo arquivo e canal)"
                mark_idempotency_failed(key=idempotency_key, error_message=duplicate_message)
                errors.append(duplicate_message)
                continue
        from apps.social.publishers import get_publisher

        publisher = get_publisher(platform)
        if not publisher:
            error_message = f"{platform}: publisher não implementado"
            mark_idempotency_failed(key=idempotency_key, error_message=error_message)
            errors.append(error_message)
            continue
        is_youtube_platform = str(platform).strip().upper() in YOUTUBE_PLATFORM_CODES
        ordered_youtube_credentials = _list_ordered_youtube_credentials(brand) if is_youtube_platform else []
        if is_youtube_platform and ordered_youtube_credentials:
            now = timezone.now()
            available_credentials = [
                cred
                for cred in ordered_youtube_credentials
                if not cred.quota_exceeded_until or cred.quota_exceeded_until <= now
            ]
            if not available_credentials:
                next_available_at = min(
                    [cred.quota_exceeded_until for cred in ordered_youtube_credentials if cred.quota_exceeded_until]
                )
                delay = max(60, int((next_available_at - now).total_seconds()))
                retryable_errors.append(
                    {
                        "message": (
                            f"{platform}: todas as credenciais YouTube da brand estão sem cota. "
                            "Aguardando reset automático."
                        ),
                        "retry_after_seconds": delay,
                        "reason": "quotaExceeded",
                    }
                )
                mark_idempotency_failed(
                    key=idempotency_key,
                    error_message=(
                        f"{platform}: todas as credenciais YouTube da brand estão sem cota. "
                        "Aguardando reset automático."
                    ),
                )
                continue

            published = False
            last_exception = None
            last_is_retriable = False
            last_reason = ""
            for _cred_idx, yt_cred in enumerate(available_credentials, 1):
                _attempt_timer = Timer()
                log_event(
                    logger,
                    event="publish_attempt_started",
                    correlation_id=correlation_id,
                    scheduled_post_id=post.id,
                    brand_id=_brand_id,
                    platform="youtube",
                    status="started",
                    attempt_number=_cred_idx,
                    youtube_credential_id=yt_cred.id,
                )
                try:
                    result = publisher.publish(
                        account,
                        video_path,
                        job_obj,
                        scheduled_post=post,
                        youtube_credential=yt_cred,
                    )
                    video_id = (result or {}).get("video_id")
                    if video_id:
                        external_ids[platform] = video_id
                        external_ids.pop("youtube_via_upload_post", None)
                        external_ids.pop("upload_post_youtube_terminal_failure", None)
                        external_ids.pop("upload_post_skip_after_unknown_no_id", None)
                        external_ids.pop(UPLOAD_POST_CLIENT_REQUEST_ID_KEY, None)
                    warning = (result or {}).get("warning")
                    if warning:
                        warnings.append(f"{platform}: {warning}")
                    if yt_cred.quota_exceeded_until or yt_cred.last_error:
                        yt_cred.quota_exceeded_until = None
                        yt_cred.last_error = ""
                        yt_cred.save(update_fields=["quota_exceeded_until", "last_error", "updated_at"])
                    log_event(
                        logger,
                        event="publish_attempt_succeeded",
                        correlation_id=correlation_id,
                        scheduled_post_id=post.id,
                        brand_id=_brand_id,
                        platform="youtube",
                        status="success",
                        attempt_number=_cred_idx,
                        duration_ms=_attempt_timer.elapsed_ms(),
                        external_video_id=video_id or "",
                        youtube_credential_id=yt_cred.id,
                    )
                    published = True
                    mark_idempotency_success(
                        key=idempotency_key,
                        result_payload={
                            "platform": platform,
                            "publisher": "native",
                            "external_ids": (
                                {platform: str(video_id)}
                                if video_id
                                else {}
                            ),
                            "remove_external_ids": [
                                "youtube_via_upload_post",
                                "upload_post_youtube_terminal_failure",
                                "upload_post_skip_after_unknown_no_id",
                                UPLOAD_POST_CLIENT_REQUEST_ID_KEY,
                            ],
                            "provider_response": result or {},
                        },
                    )
                    break
                except Exception as e:
                    reason = str(getattr(e, "reason", "") or "").strip()
                    is_retriable = bool(getattr(e, "retriable", False))
                    msg = str(e)
                    last_exception = e
                    last_is_retriable = is_retriable
                    last_reason = reason
                    log_event(
                        logger,
                        event="publish_attempt_failed",
                        correlation_id=correlation_id,
                        scheduled_post_id=post.id,
                        brand_id=_brand_id,
                        platform="youtube",
                        status="error",
                        attempt_number=_cred_idx,
                        duration_ms=_attempt_timer.elapsed_ms(),
                        error=msg,
                        reason=reason,
                        retriable=is_retriable,
                        youtube_credential_id=yt_cred.id,
                    )
                    if "invalid_grant" in msg.lower() or str(reason).lower() in (
                        "invalidgrant",
                        "invalid_grant",
                    ):
                        external_ids["youtube_native_invalid_grant"] = True
                        external_ids["youtube_native_invalid_grant_at"] = timezone.now().isoformat()
                        log_event(
                            logger,
                            event="youtube_native_invalid_grant",
                            correlation_id=correlation_id,
                            scheduled_post_id=post.id,
                            brand_id=_brand_id,
                            platform="youtube",
                            status="credential_failure",
                            youtube_credential_id=yt_cred.id,
                        )
                    if not is_retriable and (
                        "sem tokens" in msg.lower()
                        or "oauth do youtube não configurado" in msg.lower()
                        or "oauth do youtube nao configurado" in msg.lower()
                    ):
                        yt_cred.last_error = f"Credencial ignorada: {msg}"
                        yt_cred.save(update_fields=["last_error", "updated_at"])
                        continue
                    if is_retriable and reason == "quotaExceeded":
                        delay = int(getattr(e, "retry_after_seconds", 0) or 3600)
                        yt_cred.quota_exceeded_until = timezone.now() + timedelta(seconds=max(delay, 300))
                        yt_cred.last_error = f"quotaExceeded: {e}"
                        yt_cred.save(update_fields=["quota_exceeded_until", "last_error", "updated_at"])
                        continue
                    # Any other error: save on credential and try next
                    yt_cred.last_error = f"{reason or 'erro'}: {msg}"[:500]
                    yt_cred.save(update_fields=["last_error", "updated_at"])
                    continue

            if published:
                continue
            # All credentials failed: schedule retry or mark error
            if last_exception is not None:
                if last_is_retriable:
                    mark_idempotency_failed(
                        key=idempotency_key,
                        error_message=f"{platform}: {last_exception}",
                    )
                    retryable_errors.append(
                        {
                            "message": f"{platform}: {last_exception}",
                            "retry_after_seconds": getattr(last_exception, "retry_after_seconds", None),
                            "reason": last_reason,
                        }
                    )
                else:
                    error_message = f"{platform}: {last_exception}"
                    mark_idempotency_failed(key=idempotency_key, error_message=error_message)
                    errors.append(error_message)
            continue

            next_available_at = min(
                [cred.quota_exceeded_until for cred in ordered_youtube_credentials if cred.quota_exceeded_until]
            )
            delay = max(60, int((next_available_at - timezone.now()).total_seconds()))
            retryable_errors.append(
                {
                    "message": (
                        f"{platform}: cota excedida em todas as credenciais da brand. "
                        "Postagem pausada até o reset da cota."
                    ),
                    "retry_after_seconds": delay,
                    "reason": "quotaExceeded",
                }
            )
            continue

        try:
            result = publisher.publish(account, video_path, job_obj, scheduled_post=post)
            video_id = (result or {}).get("video_id")
            if video_id:
                external_ids[platform] = video_id
                if platform in ("YT", "YTB"):
                    external_ids.pop("youtube_via_upload_post", None)
                    external_ids.pop("upload_post_youtube_terminal_failure", None)
                    external_ids.pop("upload_post_skip_after_unknown_no_id", None)
                    external_ids.pop(UPLOAD_POST_CLIENT_REQUEST_ID_KEY, None)
            warning = (result or {}).get("warning")
            if warning:
                warnings.append(f"{platform}: {warning}")
            mark_idempotency_success(
                key=idempotency_key,
                result_payload={
                    "platform": platform,
                    "publisher": "native",
                    "external_ids": (
                        {platform: str(video_id)}
                        if video_id
                        else {}
                    ),
                    "remove_external_ids": (
                        [
                            "youtube_via_upload_post",
                            "upload_post_youtube_terminal_failure",
                            "upload_post_skip_after_unknown_no_id",
                            UPLOAD_POST_CLIENT_REQUEST_ID_KEY,
                        ]
                        if platform in ("YT", "YTB")
                        else []
                    ),
                    "provider_response": result or {},
                },
            )
        except Exception as e:
            msg = str(e)
            reason = str(getattr(e, "reason", "") or "").strip()
            if str(platform).strip().upper() in YOUTUBE_PLATFORM_CODES and (
                "invalid_grant" in msg.lower()
                or str(reason).lower() in ("invalidgrant", "invalid_grant")
            ):
                external_ids["youtube_native_invalid_grant"] = True
                external_ids["youtube_native_invalid_grant_at"] = timezone.now().isoformat()
                log_event(
                    logger,
                    event="youtube_native_invalid_grant",
                    correlation_id=correlation_id,
                    scheduled_post_id=post.id,
                    brand_id=_brand_id,
                    platform="youtube",
                    status="credential_failure",
                )
            if getattr(e, "retriable", False):
                mark_idempotency_failed(key=idempotency_key, error_message=f"{platform}: {e}")
                retryable_errors.append(
                    {
                        "message": f"{platform}: {e}",
                        "retry_after_seconds": getattr(e, "retry_after_seconds", None),
                        "reason": str(getattr(e, "reason", "") or ""),
                    }
                )
            else:
                error_message = f"{platform}: {e}"
                mark_idempotency_failed(key=idempotency_key, error_message=error_message)
                errors.append(error_message)

    resultado.social_account_changed = social_account_changed
    return resultado
