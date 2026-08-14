"""Finalização da publicação (refactor.md R-12 / D-01).

Quarta e última fatia de `_run_post_to_platforms`. Recebe o resultado dos ramos anteriores
e decide **em que estado o post termina**:

  - só erros reagendáveis → volta para `PENDING` com nova data, e a tentativa pode ou não
    ser consumida (cota e limite de upload não consomem: o problema é do provedor, não do
    conteúdo);
  - erros definitivos → `FAILED`, com o motivo agregado;
  - sucesso → `posting_state` faz a transição atômica dos 4 modelos (R-06), a mídia local
    é limpa e o primeiro comentário é agendado.

⚠ **O dict de retorno é contrato**, campo por campo: `status`, `errors`, `warnings`,
`external_ids`, `retry_at`. Ele é lido pelo chamador, aparece em log e é afirmado nos
testes do R-03 e do R-04.

A escolha do atraso do reagendamento também é contrato de comportamento: cota espera 1h,
limite de upload espera 24h, idempotência em progresso espera o intervalo curto — e em
todos os casos o pedido explícito do provedor (`retry_after_seconds`) só aumenta a espera,
nunca diminui.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.utils import timezone

from apps.common.metrics import (
    publish_duration_ms,
    publish_failures_total,
    publish_quota_exhaustion_attempts_total,
)
from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.models import FactoryPostingAttemptLog, ScheduledPost
from apps.social.services.publish_targets import _resolve_post_target_brand

logger = logging.getLogger(__name__)


@dataclass
class FinalizeContext:
    """O estado acumulado pelos ramos de publicação, na forma em que a finalização o lê."""

    post: ScheduledPost
    brand: Any
    errors: list[str]
    warnings: list[str]
    retryable_errors: list[dict]
    external_ids: dict
    upload_fingerprint: str
    social_account_changed: bool
    correlation_id: str
    brand_id: int | None
    current_attempt: int
    timer: Timer


def finalize_publish(ctx: FinalizeContext) -> dict:
    """Consolida o resultado e devolve o dict final da task, campo por campo igual."""
    # Import adiado: estes ainda moram em `tasks.py` e saem no R-13.
    from apps.social.tasks import (
        IDEMPOTENCY_IN_PROGRESS_DELAY_SEC,
        THUMBNAIL_BATCH_DELAY_SEC,
        YOUTUBE_QUOTA_MAX_RETRIES,
        _fail_expired_factory_slot,
        _platforms_are_youtube_only,
        _sync_factory_posting_schedule,
        upload_thumbnails_after_batch_task,
    )

    post = ctx.post
    brand = ctx.brand
    errors = ctx.errors
    warnings = ctx.warnings
    retryable_errors = ctx.retryable_errors
    external_ids = ctx.external_ids
    upload_fingerprint = ctx.upload_fingerprint
    social_account_changed = ctx.social_account_changed
    correlation_id = ctx.correlation_id
    _brand_id = ctx.brand_id
    current_attempt = ctx.current_attempt
    _timer = ctx.timer

    if retryable_errors and not errors:
        has_quota_exceeded = any(
            (item.get("reason") or "").strip() == "quotaExceeded"
            for item in retryable_errors
        )
        has_upload_limit_exceeded = any(
            (item.get("reason") or "").strip() == "uploadLimitExceeded"
            for item in retryable_errors
        )
        has_min_interval_not_reached = any(
            (item.get("reason") or "").strip() == "minIntervalNotReached"
            for item in retryable_errors
        )
        has_idempotency_in_progress = any(
            (item.get("reason") or "").strip() == "idempotencyInProgress"
            for item in retryable_errors
        )
        has_upload_post_reconciliation_pending = any(
            (item.get("reason") or "").strip() == "uploadPostReconciliationPending"
            for item in retryable_errors
        )
        if has_quota_exceeded:
            q = int(getattr(post, "youtube_quota_retry_count", 0) or 0) + 1
            post.youtube_quota_retry_count = q
            publish_quota_exhaustion_attempts_total.inc()
            if q > YOUTUBE_QUOTA_MAX_RETRIES:
                errors.extend([item["message"] for item in retryable_errors])
        next_retry = int(post.retry_count or 0) + 1
        should_not_consume_attempt = (
            has_quota_exceeded
            or has_upload_limit_exceeded
            or has_min_interval_not_reached
            or has_idempotency_in_progress
            or has_upload_post_reconciliation_pending
        )
        # One retry for upload/title errors; token/quota errors do not consume attempt
        if not errors and not should_not_consume_attempt and next_retry > 1:
            errors.extend([item["message"] for item in retryable_errors])
        elif not errors:
            requested_delays = [
                int(item["retry_after_seconds"])
                for item in retryable_errors
                if item.get("retry_after_seconds")
            ]
            # quotaExceeded / uploadLimitExceeded: wait for reset (no hard failure).
            if has_quota_exceeded:
                delay = max([3600] + requested_delays)
            elif has_upload_limit_exceeded:
                delay = max([24 * 3600] + requested_delays)
            elif has_min_interval_not_reached:
                delay = max([60] + requested_delays)
            elif has_idempotency_in_progress:
                delay = max([IDEMPOTENCY_IN_PROGRESS_DELAY_SEC] + requested_delays)
            elif has_upload_post_reconciliation_pending:
                delay = max([120] + requested_delays)
            else:
                delay = max([300] + requested_delays)
            msg = " ; ".join([item["message"] for item in retryable_errors])
            next_retry_at = timezone.now() + timedelta(seconds=delay)
            expired_result = _fail_expired_factory_slot(
                post,
                correlation_id=correlation_id,
                brand_id=_brand_id,
                current_attempt=current_attempt,
                duration_ms=_timer.elapsed_ms(),
                reason="A próxima tentativa automática cairia depois do horário do slot.",
                check_time=next_retry_at,
            )
            if expired_result is not None:
                return expired_result
            post.status = "PENDING"
            if should_not_consume_attempt:
                post.retry_count = int(post.retry_count or 0)
            else:
                post.retry_count = next_retry
            post.scheduled_at = next_retry_at
            if has_quota_exceeded:
                post.error = (
                    "Cota do YouTube excedida (quotaExceeded). "
                    f"Nova tentativa automática em {delay}s. {msg}"
                )
            elif has_min_interval_not_reached:
                post.error = (
                    "Intervalo mínimo entre publicações ainda não cumprido. "
                    f"Nova tentativa automática em {delay}s. {msg}"
                )
            elif has_idempotency_in_progress:
                post.error = (
                    "Publicação aguardando conclusão de uma execução idempotente já iniciada. "
                    f"Nova tentativa automática em {delay}s. {msg}"
                )
            elif has_upload_post_reconciliation_pending:
                post.error = (
                    "Aguardando reconciliação do Upload Post antes do fallback nativo. "
                    f"Nova tentativa em {delay}s. {msg}"
                )
            else:
                post.error = f"Falha temporária. 1 tentativa automática em {delay}s. Reagende manualmente se persistir. {msg}"
            post.upload_fingerprint = upload_fingerprint
            post.external_ids = external_ids
            retry_fields = [
                "status",
                "retry_count",
                "youtube_quota_retry_count",
                "scheduled_at",
                "error",
                "upload_fingerprint",
                "external_ids",
            ]
            if social_account_changed:
                retry_fields.append("social_account")
            post.save(update_fields=retry_fields)
            try:
                FactoryPostingAttemptLog.objects.create(
                    posting_schedule=post.factory_schedule,
                    attempt_number=current_attempt,
                    started_at=timezone.now(),
                    finished_at=timezone.now(),
                    result="ERROR",
                    error_message=msg,
                    provider_response={},
                )
            except Exception:
                pass
            log_event(
                logger,
                event="publish_failed",
                correlation_id=correlation_id,
                scheduled_post_id=post.id,
                brand_id=_brand_id,
                platform="youtube",
                status="error",
                duration_ms=_timer.elapsed_ms(),
                error=msg,
                attempt_number=current_attempt,
                retry_scheduled_in_seconds=delay,
            )
            _sync_factory_posting_schedule(post)
            return {
                "status": post.status,
                "retry_scheduled_in_seconds": delay,
                "errors": [item["message"] for item in retryable_errors],
            }
    if errors:
        post.status = "FAILED"
        all_errors = errors + warnings
        post.error = "; ".join(all_errors)
    else:
        post.status = "DONE"
        post.retry_count = 0
        post.youtube_quota_retry_count = 0
        post.posted_at = timezone.now()
        # Clear the previous attempt's error: "error" is in update_fields below, so a
        # stale message would be written back alongside status=DONE (R-23).
        post.error = "; ".join(warnings) if warnings else ""
    post.upload_fingerprint = upload_fingerprint
    post.external_ids = external_ids
    update_fields = [
        "status",
        "error",
        "posted_at",
        "upload_fingerprint",
        "external_ids",
        "retry_count",
        "youtube_quota_retry_count",
    ]
    if social_account_changed:
        update_fields.append("social_account")
    post.save(update_fields=update_fields)
    try:
        FactoryPostingAttemptLog.objects.create(
            posting_schedule=post.factory_schedule,
            attempt_number=current_attempt,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            result="SUCCESS" if post.status == "DONE" else "ERROR",
            error_message=post.error or "",
            provider_response={"external_ids": post.external_ids or {}},
        )
    except Exception:
        pass
    _external_video_id = str(
        (post.external_ids or {}).get("YT") or (post.external_ids or {}).get("YTB") or ""
    )
    if post.status == "DONE":
        publish_duration_ms.observe(_timer.elapsed_ms())
        log_event(
            logger,
            event="publish_finished",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            brand_id=_brand_id,
            platform="youtube",
            status="success",
            duration_ms=_timer.elapsed_ms(),
            attempt_number=current_attempt,
            external_video_id=_external_video_id,
        )
    else:
        publish_failures_total.inc()
        log_event(
            logger,
            event="publish_finished",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            brand_id=_brand_id,
            platform="youtube",
            status="error",
            duration_ms=_timer.elapsed_ms(),
            attempt_number=current_attempt,
            error=post.error or "",
        )
    _sync_factory_posting_schedule(post)

    # Manual posts (retry, run_scheduled_posts_now): schedule cover upload (long-form only)
    if post.status == "DONE" and _platforms_are_youtube_only(post.platforms):
        brand = _resolve_post_target_brand(post)
        platforms = post.platforms or []
        is_short = "YT" in platforms and "YTB" not in platforms
        qualifies = not is_short
        if qualifies and brand and post.auto_cut_corte_id and getattr(post.auto_cut_corte, "thumbnail", None):
            video_id = str((post.external_ids or {}).get("YT") or (post.external_ids or {}).get("YTB") or "")
            if video_id:
                upload_thumbnails_after_batch_task.apply_async(
                    args=[brand.id],
                    kwargs={"post_ids": [post.id]},
                    countdown=THUMBNAIL_BATCH_DELAY_SEC,
                )
    return {
        "status": post.status,
        "errors": errors,
        "error": post.error or ("; ".join(errors) if errors else ""),
        "external_ids": external_ids,
    }
