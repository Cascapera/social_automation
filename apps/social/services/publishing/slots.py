"""Slots da factory, agendamento e mídia do post (refactor.md R-13 / D-01).

O que sobrou de `apps/social/tasks.py` depois das quatro fatias: os auxiliares que cuidam
do **slot** (o horário reservado para publicar), do espelho dele em
`FactoryPostingSchedule`, da limpeza da mídia local e da numeração diária de vídeos.

Dois deles carregam regra que não é óbvia:

- `_fail_expired_factory_slot` decide se ainda vale publicar. Slot vencido não vira falha
  do vídeo: o conteúdo continua bom, o horário é que passou.
- `_replace_ambiguous_short_slot` troca o vídeo de um slot quando o provedor deixou o envio
  num estado ambíguo. É o mecanismo que evita o pior caso — o mesmo vídeo publicado duas
  vezes — ao custo de trocar o conteúdo daquele horário.

Movidos no R-13, sem alteração de corpo.
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.metrics import publish_failures_total
from apps.jobs.logging_utils import log_event
from apps.jobs.models import (
    DailyPostingPlanItem,
    FactoryPostingAttemptLog,
    FactoryPostingSchedule,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.jobs.services.factory_scheduler import (
    allocate_inventory_item_to_slot,
    pick_inventory_item_for_slot,
)
from apps.social.services.posting_state import mark_posted

logger = logging.getLogger(__name__)

SHORT_SLOT_MAX_AUTOMATIC_REPLACEMENTS = 1


def _cleanup_local_media_if_possible(post: ScheduledPost) -> None:
    """
    Remove local files after successful posting to save storage.
    Only cleans when there are no other active schedules for the same source.
    """
    try:
        active_statuses = ["PENDING", "POSTING"]
        if post.job_id:
            has_other_active = ScheduledPost.objects.filter(
                job_id=post.job_id,
                status__in=active_statuses,
            ).exclude(id=post.id).exists()
            if has_other_active:
                return
            try:
                output = post.job.output
            except Exception:
                output = None
            if output and output.file:
                output.file.delete(save=True)
            return

        if post.auto_cut_corte_id:
            has_other_active = ScheduledPost.objects.filter(
                auto_cut_corte_id=post.auto_cut_corte_id,
                status__in=active_statuses,
            ).exclude(id=post.id).exists()
            if has_other_active:
                return
            corte = post.auto_cut_corte
            if corte and corte.file:
                corte.file.delete(save=False)
                corte.file = None
            if corte and getattr(corte, "thumbnail", None):
                corte.thumbnail.delete(save=False)
                corte.thumbnail = None
            if corte:
                corte.save(update_fields=["file", "thumbnail"])
    except Exception:
        logger.exception("Failed to clean local media for ScheduledPost=%s", post.id)


def _youtube_day_video_index(
    account,
    day_start_utc: datetime,
    day_end_utc: datetime,
    youtube_credential=None,
) -> dict[str, dict]:
    """
    Index channel videos for the day (published and scheduled).
    """
    from googleapiclient.discovery import build

    from apps.social.services.youtube_credentials import get_credentials

    # Same OAuth client that issued the token (brand/global); check client causes unauthorized_client.
    creds = get_credentials(
        account,
        youtube_credential=youtube_credential,
        use_check_client=False,
    )
    youtube = build("youtube", "v3", credentials=creds)
    # Discover uploads playlist for authenticated account.
    ch_resp = youtube.channels().list(part="contentDetails", mine=True, maxResults=1).execute()
    ch_items = (ch_resp or {}).get("items") or []
    if not ch_items:
        return {}
    uploads_playlist = (
        ((ch_items[0] or {}).get("contentDetails") or {})
        .get("relatedPlaylists", {})
        .get("uploads", "")
    )
    if not uploads_playlist:
        return {}

    video_ids: list[str] = []
    page_token = None
    # Conservative limit to avoid excessive quota use.
    max_pages = settings.YOUTUBE_FULL_SCAN_MAX_PAGES
    for _ in range(max_pages):
        pl_resp = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        items = (pl_resp or {}).get("items") or []
        if not items:
            break
        for item in items:
            vid = (
                ((item.get("contentDetails") or {}).get("videoId"))
                or ((item.get("snippet") or {}).get("resourceId") or {}).get("videoId")
                or ""
            )
            if vid:
                video_ids.append(str(vid))
        page_token = (pl_resp or {}).get("nextPageToken")
        if not page_token:
            break

    # Load status/publishAt details in batches.
    index: dict[str, dict] = {}
    for start in range(0, len(video_ids), 50):
        batch_ids = video_ids[start:start + 50]
        if not batch_ids:
            continue
        v_resp = youtube.videos().list(
            part="snippet,status",
            id=",".join(batch_ids),
            maxResults=50,
        ).execute()
        for item in (v_resp or {}).get("items") or []:
            vid = str(item.get("id") or "")
            if not vid:
                continue
            snippet = item.get("snippet") or {}
            status_data = item.get("status") or {}
            publish_at_raw = status_data.get("publishAt")
            uploaded_at_raw = snippet.get("publishedAt")
            publish_at = parse_datetime(str(publish_at_raw or "")) if publish_at_raw else None
            uploaded_at = parse_datetime(str(uploaded_at_raw or "")) if uploaded_at_raw else None
            if publish_at and timezone.is_naive(publish_at):
                publish_at = timezone.make_aware(publish_at, timezone.get_current_timezone())
            if uploaded_at and timezone.is_naive(uploaded_at):
                uploaded_at = timezone.make_aware(uploaded_at, timezone.get_current_timezone())

            # In window: use publishAt when present else upload date.
            ref_dt = publish_at or uploaded_at
            if not ref_dt:
                continue
            if ref_dt < day_start_utc or ref_dt > day_end_utc:
                continue

            index[vid] = {
                "title": str(snippet.get("title") or ""),
                "privacy_status": str(status_data.get("privacyStatus") or ""),
                "publish_at": publish_at_raw or "",
                "uploaded_at": uploaded_at_raw or "",
            }
    return index


def _posted_platform_and_video_id(post: ScheduledPost) -> tuple[str, str]:
    """Plataforma e id externo a registrar no log de publicação deste post.

    A plataforma é sempre a primeira declarada no post; o id é o primeiro preenchido em
    `external_ids`, que nem sempre é o da primeira plataforma (publicação parcial).
    """
    platform = (post.platforms or ["YT"])[0] if post.platforms else "YT"
    external_video_id = ""
    for code in post.platforms or []:
        external_video_id = str((post.external_ids or {}).get(code) or "")
        if external_video_id:
            break
    return platform, external_video_id


def _sync_factory_posting_schedule(post: ScheduledPost) -> None:
    schedule = FactoryPostingSchedule.objects.filter(scheduled_post=post).select_related(
        "inventory_item", "factory", "brand"
    ).first()
    if not schedule:
        return
    item = schedule.inventory_item
    if post.status == "DONE":
        # Cópias A e B do D-02. Eram dois ramos — YouTube-only e demais canais — com o
        # mesmo efeito, divergindo só em A deduplicar o PostedVideoLog e B não. Como B
        # estava errado (log duplicado, log com id vazio), unificar em posting_state
        # apagou a diferença em vez de escolher um lado (R-07, divergência 1).
        platform, external_video_id = _posted_platform_and_video_id(post)
        mark_posted(post, platform=platform, external_video_id=external_video_id)
        return
    if post.status == "FAILED":
        quota_attempts = int(getattr(post, "youtube_quota_retry_count", 0) or 0)
        attempts = int(post.retry_count or 0) + quota_attempts
        schedule.status = "FAILED"
        schedule.attempt_count = attempts
        schedule.next_retry_at = None
        schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
        is_standalone = schedule.daily_plan_item_id is None
        item.status = "FAILED" if is_standalone else "AVAILABLE"
        item.scheduled_for = schedule.scheduled_at if is_standalone else None
        item.last_error = post.error or ""
        item.attempt_count = attempts
        item.save(update_fields=["status", "scheduled_for", "last_error", "attempt_count", "updated_at"])
        return
    quota_retries = int(getattr(post, "youtube_quota_retry_count", 0) or 0)
    if post.status == "PENDING" and (int(post.retry_count or 0) > 0 or quota_retries > 0):
        schedule.status = "PLANNED"
        schedule.attempt_count = int(post.retry_count or 0) + quota_retries
        schedule.next_retry_at = post.scheduled_at
        schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
        item.status = "SCHEDULED"
        item.last_error = post.error or ""
        item.attempt_count = int(post.retry_count or 0) + quota_retries
        item.save(update_fields=["status", "last_error", "attempt_count", "updated_at"])


def _factory_slot_deadline(post: ScheduledPost) -> datetime | None:
    try:
        schedule = getattr(post, "factory_schedule", None)
    except Exception:
        schedule = None
    # Publicação avulsa (sem daily_plan_item): não tem deadline de slot.
    if schedule and getattr(schedule, "daily_plan_item_id", None) is None:
        return None
    if schedule and getattr(schedule, "scheduled_at", None):
        return schedule.scheduled_at
    if not getattr(post, "id", None):
        return None
    schedule = (
        FactoryPostingSchedule.objects.filter(scheduled_post_id=post.id)
        .only("scheduled_at", "daily_plan_item_id")
        .first()
    )
    if not schedule or schedule.daily_plan_item_id is None:
        return None
    return schedule.scheduled_at


def _fail_expired_factory_slot(
    post: ScheduledPost,
    *,
    correlation_id: str,
    brand_id: int | None,
    current_attempt: int,
    duration_ms: float,
    reason: str,
    check_time: datetime | None = None,
) -> dict | None:
    deadline = _factory_slot_deadline(post)
    if not deadline:
        return None
    when = check_time or timezone.now()
    if when <= deadline:
        return None

    ext = dict(post.external_ids or {})
    ext["slot_expired"] = True
    ext["slot_expired_at"] = timezone.now().isoformat()
    ext["slot_deadline_at"] = deadline.isoformat()
    ext.pop("upload_post_reconciliation_state", None)
    ext.pop("upload_post_no_provider_id_check_count", None)
    ext.pop("upload_post_resend_count", None)

    post.status = "FAILED"
    post.external_ids = ext
    post.error = (
        f"Janela de postagem expirada para o slot {deadline.isoformat()}. "
        f"{(reason or '').strip()[:260]}".strip()
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
            provider_response={
                "external_ids": post.external_ids or {},
                "slot_deadline_at": deadline.isoformat(),
                "checked_at": when.isoformat(),
            },
        )
    except Exception:
        pass

    publish_failures_total.inc()
    log_event(
        logger,
        event="publish_window_expired",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        brand_id=brand_id,
        platform="youtube",
        slot_deadline_at=deadline.isoformat(),
        checked_at=when.isoformat(),
        status="error",
    )
    log_event(
        logger,
        event="publish_finished",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        brand_id=brand_id,
        platform="youtube",
        status="error",
        duration_ms=duration_ms,
        attempt_number=current_attempt,
        error=post.error,
    )
    _sync_factory_posting_schedule(post)
    return {"status": post.status, "error": post.error, "external_ids": post.external_ids or {}}


@transaction.atomic
def _replace_ambiguous_short_slot(
    post: ScheduledPost,
    *,
    current_attempt: int,
    correlation_id: str,
    duration_ms: float,
    provider_not_found: bool,
    detail: str,
    external_ids: dict,
    keep_failed_inventory_available: bool = False,
    reason_prefix_override: str = "",
) -> dict | None:
    try:
        schedule = (
            FactoryPostingSchedule.objects.select_for_update()
            .select_related("inventory_item", "factory", "brand")
            .get(scheduled_post_id=post.id)
        )
    except FactoryPostingSchedule.DoesNotExist:
        return None

    if schedule.video_type != "SHORT" or not schedule.inventory_item_id:
        return None
    # Publicação avulsa: o usuário escolheu este vídeo; não substitui por outro.
    if schedule.daily_plan_item_id is None:
        return None

    inventory_item = VideoInventoryItem.objects.select_for_update().get(pk=schedule.inventory_item_id)
    replacement_count = int((external_ids or {}).get("short_slot_replacement_count", 0) or 0)
    attempts = int(post.retry_count or 0) + int(getattr(post, "youtube_quota_retry_count", 0) or 0)
    detail_text = (detail or "").strip()
    reason_prefix = reason_prefix_override or (
        "Upload Post: request_id/job_id não localizado no provedor para este short."
        if provider_not_found
        else "Upload Post: reconciliação inconclusiva para este short."
    )
    old_external_ids = dict(external_ids or {})
    old_external_ids.pop("upload_post_reconciliation_state", None)
    old_external_ids.pop("upload_post_no_provider_id_check_count", None)
    old_external_ids.pop("upload_post_resend_count", None)
    plan_item = (
        DailyPostingPlanItem.objects.filter(pk=schedule.daily_plan_item_id).first()
        if schedule.daily_plan_item_id
        else None
    )

    replacement_item = None
    if replacement_count < SHORT_SLOT_MAX_AUTOMATIC_REPLACEMENTS:
        replacement_item = pick_inventory_item_for_slot(
            factory=schedule.factory,
            brand=schedule.brand,
            video_type="SHORT",
            exclude_item_ids={inventory_item.id},
        )

    if replacement_item:
        post_error = (
            f"{reason_prefix} Slot trocado automaticamente para outro vídeo. {detail_text[:220]}"
            if detail_text
            else f"{reason_prefix} Slot trocado automaticamente para outro vídeo."
        )
        old_external_ids["upload_post_last_status"] = (
            "provider_not_found_replaced" if provider_not_found else "no_provider_id_replaced"
        )
        post.status = "FAILED"
        post.error = post_error
        post.external_ids = old_external_ids
        post.save(update_fields=["status", "error", "external_ids"])

        inventory_item.status = "AVAILABLE" if keep_failed_inventory_available else "FAILED"
        inventory_item.scheduled_for = None
        inventory_item.last_error = post_error
        inventory_item.attempt_count = attempts
        inventory_item.save(
            update_fields=["status", "scheduled_for", "last_error", "attempt_count", "updated_at"]
        )

        replacement_external_ids = {
            "short_slot_replacement_count": replacement_count + 1,
            "short_slot_replaced_from_post_id": post.id,
            "short_slot_replaced_from_inventory_item_id": inventory_item.id,
        }
        replacement_post, schedule = allocate_inventory_item_to_slot(
            factory=schedule.factory,
            brand=schedule.brand,
            item=replacement_item,
            video_type="SHORT",
            scheduled_at=schedule.scheduled_at,
            schedule=schedule,
            plan_item=plan_item,
            correlation_id=post.correlation_id or "",
            external_ids=replacement_external_ids,
        )

        try:
            FactoryPostingAttemptLog.objects.create(
                posting_schedule=schedule,
                attempt_number=current_attempt,
                started_at=timezone.now(),
                finished_at=timezone.now(),
                result="ERROR",
                error_message=post_error,
                provider_response={
                    "external_ids": old_external_ids,
                    "replacement_post_id": replacement_post.id,
                    "replacement_inventory_item_id": replacement_item.id,
                    "failed_inventory_item_id": inventory_item.id,
                },
            )
        except Exception:
            pass

        publish_failures_total.inc()
        log_event(
            logger,
            event="upload_post_short_slot_replaced",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            replacement_post_id=replacement_post.id,
            replacement_inventory_item_id=replacement_item.id,
            failed_inventory_item_id=inventory_item.id,
            provider_status="not_found" if provider_not_found else "unknown_no_provider_id",
        )
        log_event(
            logger,
            event="publish_finished",
            correlation_id=correlation_id,
            scheduled_post_id=post.id,
            brand_id=schedule.brand_id,
            platform="youtube",
            status="error",
            duration_ms=duration_ms,
            attempt_number=current_attempt,
            error=post_error,
        )
        return {
            "status": replacement_post.status,
            "skipped": "short_slot_replaced",
            "replacement_post_id": replacement_post.id,
            "replacement_inventory_item_id": replacement_item.id,
        }

    terminal_reason = (
        "Já houve uma substituição automática anterior para este slot."
        if replacement_count >= SHORT_SLOT_MAX_AUTOMATIC_REPLACEMENTS
        else "Não havia outro short disponível para assumir o slot."
    )
    post_error = (
        f"{reason_prefix} {terminal_reason} {detail_text[:220]}"
        if detail_text
        else f"{reason_prefix} {terminal_reason}"
    )
    old_external_ids["upload_post_last_status"] = (
        "provider_not_found_terminal" if provider_not_found else "no_provider_id_terminal"
    )
    post.status = "FAILED"
    post.error = post_error
    post.external_ids = old_external_ids
    post.save(update_fields=["status", "error", "external_ids"])

    schedule.status = "FAILED"
    schedule.attempt_count = attempts
    schedule.next_retry_at = None
    schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])

    inventory_item.status = "AVAILABLE" if keep_failed_inventory_available else "FAILED"
    inventory_item.scheduled_for = None
    inventory_item.last_error = post_error
    inventory_item.attempt_count = attempts
    inventory_item.save(
        update_fields=["status", "scheduled_for", "last_error", "attempt_count", "updated_at"]
    )

    try:
        FactoryPostingAttemptLog.objects.create(
            posting_schedule=schedule,
            attempt_number=current_attempt,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            result="ERROR",
            error_message=post_error,
            provider_response={"external_ids": old_external_ids},
        )
    except Exception:
        pass

    publish_failures_total.inc()
    log_event(
        logger,
        event="upload_post_short_slot_failed",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        brand_id=schedule.brand_id,
        platform="youtube",
        reason=(
            "replacement_limit_reached"
            if replacement_count >= SHORT_SLOT_MAX_AUTOMATIC_REPLACEMENTS
            else "no_replacement_short_available"
        ),
    )
    log_event(
        logger,
        event="publish_finished",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        brand_id=schedule.brand_id,
        platform="youtube",
        status="error",
        duration_ms=duration_ms,
        attempt_number=current_attempt,
        error=post_error,
    )
    return {"status": post.status, "error": post.error, "external_ids": post.external_ids or {}}


def _remove_schedule_records_missing_on_youtube(post: ScheduledPost, reason: str) -> None:
    """
    Remove from internal schedule when item does not exist on YouTube.
    """
    schedule = FactoryPostingSchedule.objects.filter(scheduled_post=post).select_related(
        "inventory_item"
    ).first()
    if schedule:
        item = schedule.inventory_item
        item.status = "AVAILABLE"
        item.scheduled_for = None
        item.last_error = f"Removido da agenda: ausente no YouTube ({reason})."
        item.save(update_fields=["status", "scheduled_for", "last_error", "updated_at"])
        schedule.delete()
    post.delete()




