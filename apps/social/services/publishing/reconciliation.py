"""Reconciliação do Upload-Post (refactor.md R-13 / D-01).

O provedor às vezes aceita o upload e **não confirma**. Este módulo trata esse estado: o
post fica pendente, uma verificação é agendada, e o fallback nativo do YouTube fica
bloqueado até saber o que aconteceu — publicar de novo no escuro é como o mesmo vídeo sobe
duas vezes.

`_try_pending_upload_post_reconciliation` roda **antes** de qualquer nova tentativa (é uma
das guardas do preflight): se houver algo pendente da execução anterior, ela resolve antes
de deixar publicar.

Movido no R-13, sem alteração de corpo.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.brands.models import Brand
from apps.common.metrics import (
    publish_duration_ms,
    publish_failures_total,
    upload_post_reconciliation_completed_total,
    upload_post_reconciliation_runs_total,
    upload_post_unknown_results_total,
)
from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.models import FactoryPostingAttemptLog, ScheduledPost
from apps.social.services.idempotency import (
    mark_idempotency_failed,
    mark_idempotency_success,
)
from apps.social.services.publish_targets import (
    _first_youtube_platform,
    _list_ordered_youtube_credentials,
    _resolve_social_account_for_platform,
)
from apps.social.services.publishing.idempotency_keys import (
    UPLOAD_POST_CLIENT_REQUEST_ID_KEY,
    _build_upload_post_platforms,
)
from apps.social.services.publishing.slots import (
    _fail_expired_factory_slot,
    _replace_ambiguous_short_slot,
    _sync_factory_posting_schedule,
)

logger = logging.getLogger(__name__)

UPLOAD_POST_RECONCILE_BASE_DELAY_SEC = 90
UPLOAD_POST_END_OF_QUEUE_MIN_DELAY_SEC = 120
UPLOAD_POST_NO_PROVIDER_ID_RECHECKS_BEFORE_RESEND = 1
UPLOAD_POST_MAX_CONTROLLED_RESENDS = 1
UPLOAD_INTERVAL_SECONDS = 60


def _upload_post_end_of_queue_delay_seconds(
    post: ScheduledPost,
    brand: Brand | None,
    *,
    minimum_seconds: int,
) -> int:
    """
    Reagenda para o fim aproximado da fila da brand, em vez de reconsultar imediatamente.
    """
    if not brand:
        return max(int(minimum_seconds or 0), UPLOAD_INTERVAL_SECONDS)
    brand_scope = (
        Q(factory_schedule__brand_id=brand.id)
        | Q(job__brand_id=brand.id)
        | Q(auto_cut_corte__analysis__brand_id=brand.id)
    )
    pending_count = (
        ScheduledPost.objects.filter(status="PENDING")
        .filter(brand_scope)
        .exclude(id=post.id)
        .distinct()
        .count()
    )
    return max(int(minimum_seconds or 0), UPLOAD_INTERVAL_SECONDS * max(1, pending_count))


def _schedule_upload_post_unknown_reconciliation(
    post: ScheduledPost,
    *,
    brand: Brand | None,
    correlation_id: str,
    brand_id: int | None,
    current_attempt: int,
    _timer: Timer,
    upload_fingerprint: str,
    external_ids: dict,
    upload_post_keys_by_platform: dict[str, str],
    provider_request_id: str | None = None,
    client_request_id: str | None = None,
    job_id: str | None = None,
    status_code: int | None = None,
    last_status: str,
    detail: str,
) -> dict | None:
    upload_post_unknown_results_total.inc()

    provider_request_id = str(provider_request_id or "").strip() or None
    client_request_id = str(client_request_id or "").strip() or None
    job_id = str(job_id or "").strip() or None
    detail = (detail or "").strip()
    has_provider_reference = bool(provider_request_id or job_id)
    unknown_delay = _upload_post_end_of_queue_delay_seconds(
        post,
        brand,
        minimum_seconds=UPLOAD_POST_END_OF_QUEUE_MIN_DELAY_SEC,
    )

    if provider_request_id:
        external_ids["upload_post_request_id"] = provider_request_id
    else:
        external_ids.pop("upload_post_request_id", None)
    if client_request_id:
        external_ids[UPLOAD_POST_CLIENT_REQUEST_ID_KEY] = client_request_id
    if job_id:
        external_ids["upload_post_job_id"] = job_id
    else:
        external_ids.pop("upload_post_job_id", None)
    external_ids["upload_post_reconciliation_state"] = "pending"
    external_ids["upload_post_last_status"] = last_status
    external_ids["upload_post_last_checked_at"] = timezone.now().isoformat()
    if not has_provider_reference:
        external_ids["upload_post_no_provider_id_check_count"] = 0
    else:
        external_ids.pop("upload_post_no_provider_id_check_count", None)

    idem_snapshot = {
        k: external_ids[k]
        for k in (
            "upload_post_request_id",
            UPLOAD_POST_CLIENT_REQUEST_ID_KEY,
            "upload_post_job_id",
            "upload_post_reconciliation_state",
            "upload_post_last_status",
            "upload_post_last_checked_at",
            "upload_post_no_provider_id_check_count",
            "upload_post_resend_count",
        )
        if k in external_ids
    }
    for idempotency_key in upload_post_keys_by_platform.values():
        if has_provider_reference:
            mark_idempotency_success(
                key=idempotency_key,
                result_payload={
                    "publisher": "upload_post",
                    "upload_post_reconciliation_pending": True,
                    "external_ids": idem_snapshot,
                },
            )
        else:
            mark_idempotency_failed(
                key=idempotency_key,
                error_message=(
                    "Upload Post aceitou/retornou resultado incerto sem request_id/job_id do provedor; "
                    "não reutilizar este estado para posts novos"
                ),
                result_payload={
                    "publisher": "upload_post",
                    "upload_post_reconciliation_pending": True,
                    "external_ids": idem_snapshot,
                },
            )

    log_event(
        logger,
        event="upload_post_unknown_result",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        brand_id=brand_id,
        platform="youtube",
        status="unknown",
        status_code=status_code,
        detail=detail[:400],
    )
    log_event(
        logger,
        event="upload_post_reconciliation_scheduled",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        delay_seconds=unknown_delay,
    )
    next_check_at = timezone.now() + timedelta(seconds=unknown_delay)
    expired_result = _fail_expired_factory_slot(
        post,
        correlation_id=correlation_id,
        brand_id=brand_id,
        current_attempt=current_attempt,
        duration_ms=_timer.elapsed_ms(),
        reason=(
            "O resultado incerto do Upload Post só seria reavaliado depois do horário do slot."
            if has_provider_reference
            else "A confirmação do Upload Post sem IDs do provedor só seria reavaliada depois do horário do slot."
        ),
        check_time=next_check_at,
    )
    if expired_result is not None:
        return expired_result

    post.status = "PENDING"
    post.scheduled_at = next_check_at
    if has_provider_reference:
        post.error = (
            "Upload Post: resultado incerto (timeout/rede/código intermediário). "
            "Confirmando status no provedor no fim da fila antes de nova ação."
        )
    else:
        post.error = (
            "Upload Post: confirmação recebida sem request_id/job_id do provedor. "
            "Reavaliando no fim da fila antes de nova ação."
        )
    post.upload_fingerprint = upload_fingerprint
    post.external_ids = external_ids
    post.save(
        update_fields=[
            "status",
            "scheduled_at",
            "error",
            "upload_fingerprint",
            "external_ids",
        ]
    )
    _sync_factory_posting_schedule(post)
    log_event(
        logger,
        event="publish_finished",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        brand_id=brand_id,
        platform="youtube",
        status="waiting",
        duration_ms=_timer.elapsed_ms(),
        attempt_number=current_attempt,
    )
    return {
        "status": post.status,
        "skipped": "upload_post_unknown_awaiting_reconciliation",
        "external_ids": external_ids,
    }


def _native_youtube_fallback_available(post: ScheduledPost, brand: Brand | None) -> bool:
    if not brand:
        return False
    yt_platform = _first_youtube_platform(post.platforms or [])
    if not yt_platform:
        return False
    if _resolve_social_account_for_platform(post, brand, yt_platform):
        return True
    return bool(_list_ordered_youtube_credentials(brand))


def _try_pending_upload_post_reconciliation(
    post: ScheduledPost,
    brand: Brand | None,
    *,
    correlation_id: str,
    brand_id: int | None,
    current_attempt: int,
    _timer: Timer,
) -> dict | None:
    """
    Quando o post está PENDING com upload_post_reconciliation_state=pending, consulta o Upload Post
    antes de tomar POSTING e reenviar vídeo.
    """
    from apps.social.services.upload_post_reconciliation import (
        EXT_UPLOAD_POST_RECONCILIATION_STATE,
        RECONCILIATION_STATE_PENDING,
        ReconcileDecision,
        apply_external_ids_patch,
        merge_completed_upload_status_into_external_ids,
        reconcile_upload_post_status,
    )

    ext = dict(post.external_ids or {})
    if ext.get(EXT_UPLOAD_POST_RECONCILIATION_STATE) != RECONCILIATION_STATE_PENDING:
        return None

    expired_result = _fail_expired_factory_slot(
        post,
        correlation_id=correlation_id,
        brand_id=brand_id,
        current_attempt=current_attempt,
        duration_ms=_timer.elapsed_ms(),
        reason="O horário do slot já passou antes de concluir a reconciliação do Upload Post.",
    )
    if expired_result is not None:
        return expired_result

    upload_post_reconciliation_runs_total.inc()
    up_list = _build_upload_post_platforms(brand, post) if brand else []
    needs_youtube = "YOUTUBE" in up_list

    outcome = reconcile_upload_post_status(external_ids=ext, needs_youtube=needs_youtube)
    apply_external_ids_patch(ext, outcome.external_ids_patch)
    next_delay = _upload_post_end_of_queue_delay_seconds(
        post,
        brand,
        minimum_seconds=max(UPLOAD_POST_RECONCILE_BASE_DELAY_SEC, int(outcome.next_delay_seconds or 90)),
    )

    log_event(
        logger,
        event="upload_post_reconciliation_completed",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        brand_id=brand_id,
        platform="youtube",
        status=getattr(outcome.decision, "value", str(outcome.decision)),
        detail=(outcome.detail or "")[:400],
    )

    if outcome.decision == ReconcileDecision.WAIT:
        next_check_at = timezone.now() + timedelta(seconds=next_delay)
        expired_result = _fail_expired_factory_slot(
            post,
            correlation_id=correlation_id,
            brand_id=brand_id,
            current_attempt=current_attempt,
            duration_ms=_timer.elapsed_ms(),
            reason="A reconciliação do Upload Post só teria nova tentativa depois do horário do slot.",
            check_time=next_check_at,
        )
        if expired_result is not None:
            return expired_result
        log_event(
            logger,
            event="upload_post_fallback_blocked",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            reason="provider_status_pending_or_transient",
        )
        post.external_ids = ext
        post.error = (
            f"Upload Post: confirmação pendente. {(outcome.detail or '')[:220]} "
            f"Nova verificação em {next_delay}s."
        )
        post.scheduled_at = next_check_at
        post.save(update_fields=["external_ids", "scheduled_at", "error"])
        log_event(
            logger,
            event="publish_finished",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            brand_id=brand_id,
            platform="youtube",
            status="waiting",
            duration_ms=_timer.elapsed_ms(),
            attempt_number=current_attempt,
        )
        return {"status": post.status, "skipped": "upload_post_reconciliation_wait"}

    if outcome.decision in (ReconcileDecision.NO_PROVIDER_ID, ReconcileDecision.PROVIDER_NOT_FOUND):
        provider_not_found = outcome.decision == ReconcileDecision.PROVIDER_NOT_FOUND
        no_provider_id_checks = int(ext.get("upload_post_no_provider_id_check_count", 0) or 0) + 1
        resend_count = int(ext.get("upload_post_resend_count", 0) or 0)
        ext["upload_post_no_provider_id_check_count"] = no_provider_id_checks
        unknown_result_detail = (
            "request_id_not_found_in_provider" if provider_not_found else "no_request_id_or_job_id"
        )
        log_event(
            logger,
            event="upload_post_unknown_result",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            detail=unknown_result_detail,
        )
        if resend_count >= UPLOAD_POST_MAX_CONTROLLED_RESENDS:
            short_replacement_result = _replace_ambiguous_short_slot(
                post,
                current_attempt=current_attempt,
                correlation_id=correlation_id,
                duration_ms=_timer.elapsed_ms(),
                provider_not_found=provider_not_found,
                detail=outcome.detail or "",
                external_ids=ext,
            )
            if short_replacement_result is not None:
                return short_replacement_result
            if _native_youtube_fallback_available(post, brand):
                ext.pop(EXT_UPLOAD_POST_RECONCILIATION_STATE, None)
                ext.pop("upload_post_no_provider_id_check_count", None)
                ext["upload_post_last_status"] = (
                    "provider_not_found_fallback_native"
                    if provider_not_found
                    else "no_provider_id_fallback_native"
                )
                ext["upload_post_last_checked_at"] = timezone.now().isoformat()
                ext["upload_post_youtube_terminal_failure"] = True
                ext["upload_post_skip_after_unknown_no_id"] = True
                post.external_ids = ext
                post.error = (
                    "Upload Post: request_id/job_id não localizado no provedor após novo envio controlado. "
                    "Tentando fallback nativo do YouTube."
                    if provider_not_found
                    else
                    "Upload Post: resultado incerto sem request_id/job_id após novo envio controlado. "
                    "Tentando fallback nativo do YouTube."
                )
                post.save(update_fields=["external_ids", "error"])
                log_event(
                    logger,
                    event="upload_post_fallback_allowed",
                    correlation_id=correlation_id,
                    scheduled_post_id=post.id,
                    brand_id=brand_id,
                    platform="youtube",
                    reason=(
                        "provider_not_found_after_resend"
                        if provider_not_found
                        else "unknown_no_provider_id_after_resend"
                    ),
                )
                return None
            ext.pop(EXT_UPLOAD_POST_RECONCILIATION_STATE, None)
            ext.pop("upload_post_no_provider_id_check_count", None)
            ext["upload_post_last_status"] = (
                "provider_not_found_terminal" if provider_not_found else "no_provider_id_terminal"
            )
            ext["upload_post_last_checked_at"] = timezone.now().isoformat()
            post.status = "FAILED"
            post.external_ids = ext
            post.error = (
                "Upload Post: request_id/job_id não localizado no provedor mesmo após novo envio controlado. "
                "Vídeo devolvido ao inventário para evitar loop infinito."
                if provider_not_found
                else
                "Upload Post: resultado incerto sem request_id/job_id mesmo após novo envio controlado. "
                "Vídeo devolvido ao inventário para evitar loop infinito."
            )
            post.save(update_fields=["status", "external_ids", "error"])
            try:
                FactoryPostingAttemptLog.objects.create(
                    posting_schedule=post.factory_schedule,
                    attempt_number=current_attempt,
                    started_at=timezone.now(),
                    finished_at=timezone.now(),
                    result="ERROR",
                    error_message=post.error,
                    provider_response={"external_ids": post.external_ids or {}},
                )
            except Exception:
                pass
            publish_failures_total.inc()
            log_event(
                logger,
                event="upload_post_reconciliation_abandoned",
                correlation_id=correlation_id,
                scheduled_post_id=post.id,
                brand_id=brand_id,
                platform="youtube",
                resend_count=resend_count,
            )
            log_event(
                logger,
                event="publish_finished",
                correlation_id=correlation_id,
                scheduled_post_id=post.id,
                brand_id=brand_id,
                platform="youtube",
                status="error",
                duration_ms=_timer.elapsed_ms(),
                attempt_number=current_attempt,
                error=post.error,
            )
            _sync_factory_posting_schedule(post)
            return {"status": post.status, "error": post.error, "external_ids": post.external_ids or {}}

        if no_provider_id_checks > UPLOAD_POST_NO_PROVIDER_ID_RECHECKS_BEFORE_RESEND:
            ext["upload_post_resend_count"] = resend_count + 1
            ext["upload_post_no_provider_id_check_count"] = 0
            ext.pop(EXT_UPLOAD_POST_RECONCILIATION_STATE, None)
            ext.pop("upload_post_last_status", None)
            ext.pop("upload_post_last_checked_at", None)
            ext.pop("upload_post_request_id", None)
            ext.pop("upload_post_job_id", None)
            post.external_ids = ext
            post.error = (
                "Upload Post: request_id/job_id não localizado no provedor; iniciando novo envio controlado."
                if provider_not_found
                else
                "Upload Post: sem request_id/job_id após confirmação tardia; iniciando novo envio controlado."
            )
            post.scheduled_at = timezone.now()
            post.save(update_fields=["external_ids", "scheduled_at", "error"])
            log_event(
                logger,
                event="upload_post_reconciliation_resend_started",
                correlation_id=correlation_id,
                scheduled_post_id=post.id,
                brand_id=brand_id,
                platform="youtube",
                resend_count=ext["upload_post_resend_count"],
            )
            return None
        next_check_at = timezone.now() + timedelta(seconds=next_delay)
        expired_result = _fail_expired_factory_slot(
            post,
            correlation_id=correlation_id,
            brand_id=brand_id,
            current_attempt=current_attempt,
            duration_ms=_timer.elapsed_ms(),
            reason="O slot expiraria antes de uma nova checagem do resultado incerto do Upload Post.",
            check_time=next_check_at,
        )
        if expired_result is not None:
            return expired_result
        post.external_ids = ext
        post.error = (
            "Upload Post: request_id/job_id não localizado no provedor; "
            f"nova tentativa de verificação no fim da fila em {next_delay}s."
            if provider_not_found
            else
            "Upload Post: resultado incerto e sem request_id/job_id persistido; "
            f"nova tentativa de verificação no fim da fila em {next_delay}s."
        )
        post.scheduled_at = next_check_at
        post.save(update_fields=["external_ids", "scheduled_at", "error"])
        log_event(
            logger,
            event="publish_finished",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            brand_id=brand_id,
            platform="youtube",
            status="waiting",
            duration_ms=_timer.elapsed_ms(),
            attempt_number=current_attempt,
        )
        return {
            "status": post.status,
            "skipped": "upload_post_provider_not_found" if provider_not_found else "upload_post_no_provider_id",
        }

    if outcome.decision == ReconcileDecision.CONFIRMED_FAILURE:
        upload_post_reconciliation_completed_total.inc()
        ext.pop("upload_post_no_provider_id_check_count", None)
        ext.pop("upload_post_resend_count", None)
        ext["upload_post_youtube_terminal_failure"] = True
        ext.pop(EXT_UPLOAD_POST_RECONCILIATION_STATE, None)
        post.external_ids = ext
        post.save(update_fields=["external_ids"])
        log_event(
            logger,
            event="upload_post_fallback_allowed",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            reason="upload_post_confirmed_failure",
        )
        return None

    if outcome.decision == ReconcileDecision.CONFIRMED_SUCCESS:
        upload_post_reconciliation_completed_total.inc()
        if not up_list:
            ext.pop(EXT_UPLOAD_POST_RECONCILIATION_STATE, None)
            ext.pop("upload_post_no_provider_id_check_count", None)
            ext.pop("upload_post_resend_count", None)
            ext.pop(UPLOAD_POST_CLIENT_REQUEST_ID_KEY, None)
            post.external_ids = ext
            post.save(update_fields=["external_ids"])
            return None
        raw = outcome.raw_status_payload or {}
        post_pf = list(post.platforms or [])
        _, all_filled = merge_completed_upload_status_into_external_ids(
            raw,
            post_platforms=post_pf,
            upload_post_platforms=up_list,
            external_ids=ext,
        )
        if outcome.youtube_video_id and needs_youtube:
            yp = _first_youtube_platform(post_pf)
            if yp:
                ext[yp] = outcome.youtube_video_id
        ext["youtube_via_upload_post"] = True
        ext.pop(EXT_UPLOAD_POST_RECONCILIATION_STATE, None)
        ext.pop("upload_post_no_provider_id_check_count", None)
        ext.pop("upload_post_resend_count", None)
        ext.pop(UPLOAD_POST_CLIENT_REQUEST_ID_KEY, None)

        if up_list and not all_filled:
            next_check_at = timezone.now() + timedelta(seconds=next_delay)
            expired_result = _fail_expired_factory_slot(
                post,
                correlation_id=correlation_id,
                brand_id=brand_id,
                current_attempt=current_attempt,
                duration_ms=_timer.elapsed_ms(),
                reason="O Upload Post concluiu parcialmente, mas a próxima verificação cairia depois do horário do slot.",
                check_time=next_check_at,
            )
            if expired_result is not None:
                return expired_result
            ext[EXT_UPLOAD_POST_RECONCILIATION_STATE] = RECONCILIATION_STATE_PENDING
            post.external_ids = ext
            post.error = "Upload Post: processamento concluído parcialmente; aguardando IDs de todas as plataformas."
            post.scheduled_at = next_check_at
            post.save(update_fields=["external_ids", "scheduled_at", "error"])
            log_event(
                logger,
                event="upload_post_fallback_blocked",
                correlation_id=correlation_id,
                scheduled_post_id=post.id,
                reason="partial_platform_ids",
            )
            return {"status": post.status, "skipped": "upload_post_partial_ids"}

        post.external_ids = ext
        post.status = "DONE"
        post.retry_count = 0
        post.youtube_quota_retry_count = 0
        post.posted_at = timezone.now()
        post.error = ""
        post.save(
            update_fields=[
                "status",
                "error",
                "posted_at",
                "external_ids",
                "retry_count",
                "youtube_quota_retry_count",
            ]
        )
        try:
            FactoryPostingAttemptLog.objects.create(
                posting_schedule=post.factory_schedule,
                attempt_number=current_attempt,
                started_at=timezone.now(),
                finished_at=timezone.now(),
                result="SUCCESS",
                error_message="",
                provider_response={"external_ids": post.external_ids or {}},
            )
        except Exception:
            pass
        publish_duration_ms.observe(_timer.elapsed_ms())
        log_event(
            logger,
            event="publish_finished",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            brand_id=brand_id,
            platform="youtube",
            status="success",
            duration_ms=_timer.elapsed_ms(),
            attempt_number=current_attempt,
            external_video_id=str(
                (post.external_ids or {}).get("YT") or (post.external_ids or {}).get("YTB") or ""
            ),
        )
        _sync_factory_posting_schedule(post)
        return {
            "status": "DONE",
            "errors": [],
            "error": "",
            "external_ids": post.external_ids or {},
        }

    return None
