"""Social network posting tasks."""
import hashlib
import logging
import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutReadyChunk
from apps.brands.models import (
    Brand,
    BrandAsset,
    BrandSocialAccount,
    BrandYouTubeCredential,
    Factory,
)
from apps.common.metrics import (
    publish_attempts_total,
    publish_failures_total,
    publish_reconciliation_duration_ms,
    publish_reconciliation_failures_total,
    publish_reconciliation_runs_total,
)
from apps.common.task_observability import instrument_celery_task
from apps.cuts.models import Cut
from apps.jobs.logging_utils import (
    Timer,
    log_event,
    new_correlation_id,
)
from apps.jobs.models import (
    FactoryPostingAttemptLog,
    FactoryPostingSchedule,
    Job,
    RenderOutput,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.jobs.services.factory_scheduler import (
    generate_daily_schedule_for_factory,
)
from apps.mediahub.models import SourceVideo
from apps.social.publishers import get_publisher
from apps.social.publishers.youtube import YouTubePublisher
from apps.social.services.posting_state import mark_posted, mark_still_scheduled
from apps.social.services.publish_targets import (
    _first_youtube_platform,
    _list_ordered_youtube_credentials,
    _platforms_are_youtube_only,
    _resolve_brand_youtube_account,
    _resolve_post_target_brand,
    _resolve_social_account_for_platform,
    _should_remove_missing_by_verify_error,
    _youtube_verify_exists_with_credential_fallback,
)
from apps.social.services.publishing.finalize import FinalizeContext, finalize_publish
from apps.social.services.publishing.idempotency_keys import (
    _build_upload_post_platforms,
)
from apps.social.services.publishing.preflight import EarlyExit, preflight
from apps.social.services.publishing.slots import (
    _cleanup_local_media_if_possible,
    _fail_expired_factory_slot,
    _remove_schedule_records_missing_on_youtube,
    _sync_factory_posting_schedule,
    _youtube_day_video_index,
)
from apps.social.services.publishing.upload_post import publish_via_upload_post
from apps.social.services.publishing.youtube_native import publish_native_platforms
from apps.social.services.youtube_credentials import get_credentials

logger = logging.getLogger(__name__)
YOUTUBE_PLATFORM_CODES = {"YT", "YTB"}
# Upload-Post rejects very large files (~250MB); longer videos above that use native YouTube API only.
UPLOAD_POST_LONG_MAX_BYTES = 250 * 1024 * 1024
BATCH_LIMIT_PER_TICK = 20
YOUTUBE_VERIFY_GRACE_SECONDS = 600
# YOUTUBE_CHECK_CLIENT_ENABLED vivia aqui, lido no import. O refactor.md o registrava como
# "área intestável" e como o ramo que o R-04 não conseguiu cobrir — mas era CÓDIGO MORTO:
# nada no repositório o lia. Removido no R-17; a configuração agora é settings.YOUTUBE_CHECK_*
# e quem decide se o cliente de check está ligado é o próprio ponto de uso.
SHORT_SLOT_MAX_AUTOMATIC_REPLACEMENTS = 1


















UPLOAD_INTERVAL_SECONDS = 60  # One video per minute on send queue
THUMBNAIL_BATCH_DELAY_SEC = 120  # Buffer after last video before thumbnail uploads
UPLOAD_POST_RETRY_COUNT = 2  # Max retries for Upload Post
UPLOAD_POST_RETRY_DELAY_SEC = 10  # Seconds between retries
UPLOAD_POST_PROVIDER_BUSY_STATUS_CODES = {499, 504}
UPLOAD_POST_PROVIDER_BUSY_RETRY_COUNT = 1
UPLOAD_POST_RECONCILE_BASE_DELAY_SEC = 90
UPLOAD_POST_END_OF_QUEUE_MIN_DELAY_SEC = 120
UPLOAD_POST_NO_PROVIDER_ID_RECHECKS_BEFORE_RESEND = 1
UPLOAD_POST_MAX_CONTROLLED_RESENDS = 1
UPLOAD_POST_CLIENT_REQUEST_ID_KEY = "upload_post_client_request_id"
YOUTUBE_PREPUBLISH_WINDOW_SECONDS = 60 * 60  # Envia para o provedor 1h antes do slot final
DAILY_SCHEDULE_GENERATION_HOURS = (9, 11, 13)  # janelas locais: tentativa principal + 2 catch-ups
# YouTube API quotaExceeded: no máximo 2 retries (3 tentativas no total); depois FAILED e inventário AVAILABLE.
YOUTUBE_QUOTA_MAX_RETRIES = 2
IDEMPOTENCY_IN_PROGRESS_DELAY_SEC = 60




@shared_task(
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=1800,
    time_limit=2100,
)
def upload_thumbnails_after_batch_task(brand_id: int, post_ids: list[int] | None = None):
    """
    Upload YouTube thumbnails for a brand after all videos in batch are published.
    Called after last video for brand (check_scheduled_posts_task schedules with countdown).
    Retry: 60s between attempts, max 2 retries (3 attempts total) per video.
    post_ids: batch post IDs (optional; if empty, all DONE for brand).
    Shorts (YT): do not send cover to YouTube (local generation still; saves quota).
    Long-form (YTB): send cover when cut has thumbnail.
    Skips posts with external_ids.youtube_via_upload_post: nesse caminho a capa já é
    enviada no mesmo multipart do Upload-Post (campo ``thumbnail``), então o reenvio
    pela API nativa seria redundante e consumiria quota desnecessariamente.
    """
    try:
        brand = Brand.objects.select_related("factory").get(id=brand_id)
    except Brand.DoesNotExist:
        logger.warning("[THUMB] Brand %s not found for thumbnail upload", brand_id)
        return {"brand_id": brand_id, "uploaded": 0, "skipped": 0, "errors": 0}

    qs = ScheduledPost.objects.filter(
        auto_cut_corte__isnull=False,
    ).select_related("auto_cut_corte", "social_account", "factory_schedule")
    if post_ids:
        qs = qs.filter(id__in=post_ids)
    else:
        qs = qs.filter(
            status="DONE",
        ).filter(
            Q(platforms__contains=["YT"]) | Q(platforms__contains=["YTB"]),
        ).filter(Q(external_ids__has_key="YT") | Q(external_ids__has_key="YTB"))

    posts = list(qs.order_by("scheduled_at", "id"))
    to_upload = []
    skipped_upload_post_youtube = 0
    for p in posts:
        b = _resolve_post_target_brand(p)
        if b and b.id == brand_id:
            if getattr(p.auto_cut_corte, "thumbnail", None):
                platforms = p.platforms or []
                is_short = "YT" in platforms and "YTB" not in platforms
                if is_short:
                    continue
                video_id = str((p.external_ids or {}).get("YT") or (p.external_ids or {}).get("YTB") or "")
                if video_id:
                    if (p.external_ids or {}).get("youtube_via_upload_post"):
                        skipped_upload_post_youtube += 1
                        logger.info(
                            "[THUMB] Skip YouTube thumbnail (YouTube via Upload Post) post_id=%s video_id=%s",
                            p.id,
                            video_id,
                        )
                        continue
                    to_upload.append((p, video_id))

    if not to_upload:
        return {
            "brand_id": brand_id,
            "uploaded": 0,
            "skipped": skipped_upload_post_youtube,
            "errors": 0,
        }

    account = BrandSocialAccount.objects.filter(
        brand=brand,
        platform__in=["YT", "YTB"],
    ).order_by("id").first()
    creds_list = _list_ordered_youtube_credentials(brand)
    cred = creds_list[0] if creds_list else None
    if not account and not cred:
        logger.warning("[THUMB] Brand %s has no YouTube account/credential", brand_id)
        return {
            "brand_id": brand_id,
            "uploaded": 0,
            "skipped": skipped_upload_post_youtube + len(to_upload),
            "errors": 0,
        }

    from googleapiclient.discovery import build


    publisher = get_publisher("YT")
    if not publisher:
        return {
            "brand_id": brand_id,
            "uploaded": 0,
            "skipped": skipped_upload_post_youtube + len(to_upload),
            "errors": 0,
        }

    token_holder = cred if cred else account
    if not token_holder.access_token and not token_holder.refresh_token:
        logger.warning("[THUMB] Brand %s: account/credential has no tokens", brand_id)
        return {
            "brand_id": brand_id,
            "uploaded": 0,
            "skipped": skipped_upload_post_youtube + len(to_upload),
            "errors": 0,
        }

    acc = account or SimpleNamespace(brand=brand, platform="YT", channel_id="")
    creds = get_credentials(acc, youtube_credential=cred if cred else None)
    youtube = build("youtube", "v3", credentials=creds)

    uploaded = 0
    errors = 0
    for post, video_id in to_upload:
        try:
            if publisher.upload_thumbnail_for_post(youtube, video_id, post):
                uploaded += 1
        except Exception as e:
            errors += 1
            logger.warning("[THUMB] Failed to upload cover video_id=%s: %s", video_id, e)

    return {
        "brand_id": brand_id,
        "uploaded": uploaded,
        "skipped": skipped_upload_post_youtube + (len(to_upload) - uploaded - errors),
        "errors": errors,
    }


























@shared_task(
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=1800,
    time_limit=2100,
)
def process_brand_posting_queue_task(brand_id: int, post_ids: list[int]):  # noqa: C901
    """
    Process a brand's post queue sequentially.
    Structured logs for observability.
    """
    try:
        brand = Brand.objects.select_related("factory").get(id=brand_id)
    except Brand.DoesNotExist:
        logger.warning("[POSTING] Brand %s not found", brand_id)
        return {"brand_id": brand_id, "posted": 0, "errors": 1}

    brand_slug = getattr(brand, "slug", "") or f"brand_{brand_id}"
    queue_size = len(post_ids)
    logger.info(
        "[POSTING] Starting media posting for brand_%s (%s)",
        brand_id,
        brand_slug,
    )
    logger.info("[POSTING] Brand video queue size (%s)", queue_size)

    posted_count = 0
    error_count = 0
    error_details: list[dict] = []  # [{post_id, errors, error}]
    remaining = queue_size

    for post_id in post_ids:
        logger.info("[POSTING] Sending to upload post")
        try:
            # Direct call: do not use apply()/get() inside task (Celery deadlock)
            result = _run_post_to_platforms(post_id)
        except Exception as e:
            error_count += 1
            err_msg = str(e)
            logger.warning("[POSTING] Error processing post %s: %s", post_id, err_msg)
            error_details.append({"post_id": post_id, "errors": [err_msg], "error": err_msg})
            remaining -= 1
            logger.info("[POSTING] Brand video queue updated (%s)", remaining)
            continue

        if isinstance(result, dict):
            if str(result.get("status", "")).upper() == "DONE":
                conf_id = ""
                ext_ids = result.get("external_ids") or {}
                for k in ("YT", "YTB", "upload_post_request_id"):
                    if ext_ids.get(k):
                        conf_id = str(ext_ids[k])
                        break
                logger.info(
                    "[POSTING] Posting confirmation received (id %s)",
                    conf_id or "ok",
                )
                posted_count += 1
            elif result.get("skipped"):
                logger.info("[POSTING] Post %s skipped: %s", post_id, result.get("skipped"))
            else:
                error_count += 1
                errs = result.get("errors")
                err = result.get("error", "unknown_error")
                if isinstance(errs, list):
                    err_list = [str(e) for e in errs]
                elif errs:
                    err_list = [str(errs)]
                else:
                    err_list = [str(err)] if err else ["unknown_error"]
                logger.warning(
                    "[POSTING] Post %s failed: %s",
                    post_id,
                    err_list,
                )
                error_details.append({"post_id": post_id, "errors": err_list, "error": err})

        remaining -= 1
        if remaining > 0:
            logger.info("[POSTING] Brand video queue updated (%s)", remaining)

    if error_count > 0:
        logger.info(
            "[POSTING] Brand_%s (%s) = %s videos posted - %s error(s)",
            brand_id,
            brand_slug,
            posted_count,
            error_count,
        )
        for ed in error_details:
            logger.error(
                "[POSTING] Error post_id=%s (YouTube API / Upload Post): %s",
                ed["post_id"],
                ed.get("errors") or ed.get("error", "?"),
            )
    else:
        logger.info(
            "[POSTING] Brand_%s (%s) = %s videos posted",
            brand_id,
            brand_slug,
            posted_count,
        )
    return {
        "brand_id": brand_id,
        "posted": posted_count,
        "total": queue_size,
        "error_count": error_count,
        "error_details": error_details,
    }


@shared_task
def check_scheduled_posts_task():
    """
    Runs every minute via Beat.
    - Picks PENDING posts (scheduled_at <= now ou dentro da janela antecipada do YouTube).
    - Groups by brand and processes each brand sequentially.
    - Structured logs for observability.
    """
    now = timezone.now()
    # Mark orphan posts (no source) as FAILED to avoid queue noise
    ScheduledPost.objects.filter(
        status="PENDING",
    ).filter(job_id__isnull=True, auto_cut_corte_id__isnull=True).update(
        status="FAILED",
        error="ScheduledPost sem origem (job/corte)",
    )
    # Skip posts without source — do not enqueue to avoid failure loops
    has_origin = Q(job_id__isnull=False) | Q(auto_cut_corte_id__isnull=False)
    due_posts = ScheduledPost.objects.filter(
        status="PENDING",
        scheduled_at__lte=now,
    ).filter(has_origin).select_related("job", "job__brand", "social_account").order_by(
        "social_account__brand_id",
        "scheduled_at",
        "id",
    )[:BATCH_LIMIT_PER_TICK]
    # Janela antecipada do YouTube/Upload Post: envia aproximadamente 1h antes do slot final.
    future_candidates = ScheduledPost.objects.filter(
        status="PENDING",
        scheduled_at__gt=now,
        scheduled_at__lte=now + timedelta(seconds=YOUTUBE_PREPUBLISH_WINDOW_SECONDS),
    ).filter(has_origin).select_related("job", "job__brand", "social_account")
    post_ids = {post.id for post in due_posts}
    for post in future_candidates:
        if _platforms_are_youtube_only(post.platforms):
            if ((post.external_ids or {}).get("upload_post_reconciliation_state") or "") == "pending":
                continue
            post_ids.add(post.id)
    post_ids_list = sorted(post_ids)
    if not post_ids_list:
        return {"checked_due": 0, "queued": 0}

    posts = list(
        ScheduledPost.objects.filter(id__in=post_ids_list)
        .select_related(
            "job",
            "job__brand",
            "social_account",
            "social_account__brand",
            "auto_cut_corte",
            "auto_cut_corte__analysis",
            "factory_schedule",
        )
        .order_by("social_account__brand_id", "scheduled_at", "id")
    )

    # Group by brand
    brand_to_posts: dict[int, list] = {}
    for p in posts:
        brand = _resolve_post_target_brand(p)
        bid = brand.id if brand else 0
        brand_to_posts.setdefault(bid, []).append(p.id)

    total_brands = len([b for b in brand_to_posts if b > 0])
    total_videos = len(post_ids_list)
    logger.info(
        "[POSTING] Starting posting cycle (brands %s, videos %s)",
        total_brands,
        total_videos,
    )

    # Process each brand sequentially (countdown to stagger)
    countdown = 0
    for brand_id, pids in sorted(brand_to_posts.items()):
        if brand_id <= 0:
            continue
        process_brand_posting_queue_task.apply_async(
            args=[brand_id, pids],
            countdown=countdown,
        )
        countdown += UPLOAD_INTERVAL_SECONDS * len(pids)

    # Thumbnails: scheduled after last video per brand
    brand_to_last_index: dict[int, int] = {}
    brand_to_post_ids: dict[int, list[int]] = {}
    for i, p in enumerate(posts):
        brand = _resolve_post_target_brand(p)
        if not brand:
            continue
        has_thumb = (
            getattr(p, "auto_cut_corte_id", None)
            and getattr(p.auto_cut_corte, "thumbnail", None)
        )
        if not has_thumb or not _platforms_are_youtube_only(p.platforms):
            continue
        platforms = p.platforms or []
        is_short = "YT" in platforms and "YTB" not in platforms
        if is_short:
            continue
        brand_to_last_index[brand.id] = i
        brand_to_post_ids.setdefault(brand.id, []).append(p.id)
    for bid, last_i in brand_to_last_index.items():
        countdown = (last_i + 1) * UPLOAD_INTERVAL_SECONDS + THUMBNAIL_BATCH_DELAY_SEC
        upload_thumbnails_after_batch_task.apply_async(
            args=[bid],
            kwargs={"post_ids": brand_to_post_ids.get(bid, [])},
            countdown=countdown,
        )

    return {
        "checked_due": due_posts.count(),
        "queued": len(post_ids_list),
        "brands": total_brands,
    }






def _factory_local_day_bounds(factory: Factory, target_date_local) -> tuple[datetime, datetime]:
    tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
    day_start_local = datetime.combine(target_date_local, time(0, 0)).replace(tzinfo=tz)
    day_end_local = (day_start_local + timedelta(days=1)) - timedelta(microseconds=1)
    return day_start_local.astimezone(UTC), day_end_local.astimezone(UTC)

def _factory_has_schedule_for_local_day(factory: Factory, target_date_local) -> bool:
    day_start_utc, day_end_utc = _factory_local_day_bounds(factory, target_date_local)
    return FactoryPostingSchedule.objects.filter(
        factory=factory,
        scheduled_at__gte=day_start_utc,
        scheduled_at__lte=day_end_utc,
    ).exists()


@shared_task(bind=True)
def generate_daily_factory_schedules_task(self):
    """
    Runs during the local 09h / 11h / 13h windows for each active factory.
    Generates the schedule for the same local day only while there is still no
    agenda created for that day, avoiding the old burst model.
    """
    task_id: str = self.request.id or ""
    correlation_id = new_correlation_id()
    task_timer = Timer()

    now = timezone.now()
    created_total = 0
    generated = 0

    for factory in Factory.objects.filter(is_active=True, scheduling_paused=False).order_by("id"):
        tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
        now_local = now.astimezone(tz)
        if now_local.hour not in DAILY_SCHEDULE_GENERATION_HOURS:
            continue

        target_date = now_local.date()
        if _factory_has_schedule_for_local_day(factory, target_date):
            continue

        factory_timer = Timer()
        try:
            result = generate_daily_schedule_for_factory(
                factory,
                now_utc=now,
                target_date=target_date,
                allow_rerun=True,
                correlation_id=correlation_id,
            )
            posts_created = int(result.get("created", 0))
            if posts_created:
                generated += 1
                created_total += posts_created
            log_event(
                logger,
                event="schedule_run_finished",
                correlation_id=correlation_id,
                task_id=task_id,
                factory_id=factory.id,
                schedule_run_id=result.get("run_id"),
                number_of_posts=posts_created,
                status="success",
                duration_ms=factory_timer.elapsed_ms(),
            )
        except Exception as exc:
            log_event(
                logger,
                event="schedule_run_failed",
                correlation_id=correlation_id,
                task_id=task_id,
                factory_id=factory.id,
                status="error",
                duration_ms=factory_timer.elapsed_ms(),
                error=str(exc),
            )
            logger.exception("Failed to generate daily schedule for factory=%s", factory.id)

    return {
        "generated_factories": generated,
        "created_posts": created_total,
        "correlation_id": correlation_id,
        "duration_ms": round(task_timer.elapsed_ms(), 2),
    }


@shared_task
def reconcile_youtube_schedules_task():
    """
    Verify YouTube uploads/schedules exist on the channel.
    If missing, re-queue without blocking others.
    Clean local media only after real confirmation.
    """
    _reconcile_cid = new_correlation_id()
    _reconcile_timer = Timer()
    log_event(
        logger,
        event="publish_reconciliation_started",
        correlation_id=_reconcile_cid,
        platform="youtube",
        status="started",
    )
    publish_reconciliation_runs_total.inc()
    checked = 0
    confirmed = 0
    requeued = 0
    failed_no_media = 0
    skipped = 0
    removed_missing = 0
    still_scheduled = 0
    try:
        now = timezone.now()
        window_start = now - timedelta(days=1)
        window_end = now + timedelta(days=1)
        # Only PENDING and POSTING: DONE already confirmed; re-check would waste quota.
        candidates = (
            ScheduledPost.objects.select_related(
                "job",
                "job__brand",
                "social_account",
                "auto_cut_corte",
                "auto_cut_corte__analysis",
            )
            .filter(
                status__in=["PENDING", "POSTING"],
                scheduled_at__gte=window_start,
                scheduled_at__lte=window_end,
            )
            .order_by("scheduled_at", "id")[:500]
        )
        for post in candidates:
            platform = _first_youtube_platform(post.platforms)
            if not platform:
                continue
            video_id = str((post.external_ids or {}).get(platform) or "")
            if not video_id:
                # No external id — cannot reconcile on channel.
                skipped += 1
                continue
            checked += 1
            brand = _resolve_post_target_brand(post)
            if not brand:
                skipped += 1
                continue
            account = _resolve_social_account_for_platform(post, brand, platform)
            if not account:
                skipped += 1
                continue
            exists = False
            verify_data = {}
            exists, verify_data = _youtube_verify_exists_with_credential_fallback(account, brand, video_id)
            if exists:
                publish_at_raw = verify_data.get("publish_at")
                publish_at = parse_datetime(str(publish_at_raw or "")) if publish_at_raw else None
                if publish_at and timezone.is_naive(publish_at):
                    publish_at = timezone.make_aware(publish_at, timezone.get_current_timezone())
                # Confirmed on YouTube (already published or scheduled): mark POSTED
                # and skip re-check to save API quota.
                confirmed += 1
                mark_posted(
                    post,
                    platform=platform,
                    external_video_id=video_id,
                    log_metadata={"youtube_verify": verify_data or {}},
                )
                _cleanup_local_media_if_possible(post)
                continue
            # Only remove when there is evidence of real absence.
            if _should_remove_missing_by_verify_error(verify_data):
                _remove_schedule_records_missing_on_youtube(post, verify_data.get("error", "unknown"))
                removed_missing += 1
                continue
            # Temporary error (auth/network/etc): keep scheduled and revalidate next cycle.
            skipped += 1
            mark_still_scheduled(
                post,
                publish_at_raw=None,
                note=f"Falha temporária na confirmação YouTube: {verify_data.get('error', 'unknown')}",
            )
    except Exception as exc:
        publish_reconciliation_failures_total.inc()
        log_event(
            logger,
            event="publish_reconciliation_failed",
            correlation_id=_reconcile_cid,
            platform="youtube",
            status="error",
            duration_ms=_reconcile_timer.elapsed_ms(),
            error=str(exc),
        )
        raise
    publish_reconciliation_duration_ms.observe(_reconcile_timer.elapsed_ms())
    log_event(
        logger,
        event="publish_reconciliation_finished",
        correlation_id=_reconcile_cid,
        platform="youtube",
        status="success",
        duration_ms=_reconcile_timer.elapsed_ms(),
        checked=checked,
        confirmed=confirmed,
        removed_missing=removed_missing,
        skipped=skipped,
    )
    return {
        "checked": checked,
        "confirmed": confirmed,
        "still_scheduled": still_scheduled,
        "requeued": requeued,
        "removed_missing": removed_missing,
        "failed_no_media": failed_no_media,
        "skipped": skipped,
    }


@shared_task
@instrument_celery_task
def reconcile_youtube_full_scan_task(factory_id: int | None = None, day_iso: str | None = None):
    """
    Daily full scan per channel:
    - scan scheduled/posted YouTube videos for the day
    - reconcile internal schedule
    - remove internal records missing on YouTube
    """
    factories = Factory.objects.filter(is_active=True)
    if factory_id:
        factories = factories.filter(id=factory_id)

    summary = {
        "factories": 0,
        "brands": 0,
        "checked": 0,
        "confirmed": 0,
        "still_scheduled": 0,
        "removed_missing": 0,
        "skipped_no_external_id": 0,
        "channel_only_videos": 0,
        "errors": [],
    }

    for factory in factories.order_by("id"):
        summary["factories"] += 1
        factory_tz = timezone.get_current_timezone()
        try:
            if getattr(factory, "timezone", ""):
                factory_tz = ZoneInfo(factory.timezone)
        except Exception:
            factory_tz = timezone.get_current_timezone()

        now_local = timezone.localtime(timezone.now(), factory_tz)
        target_day = now_local.date()
        if day_iso:
            try:
                target_day = datetime.strptime(day_iso, "%Y-%m-%d").date()
            except ValueError:
                summary["errors"].append(f"invalid day_iso: {day_iso}")
                continue

        day_start_local = timezone.make_aware(datetime.combine(target_day, datetime.min.time()), factory_tz)
        day_end_local = day_start_local + timedelta(days=1) - timedelta(microseconds=1)
        day_start_utc = day_start_local.astimezone(UTC)
        day_end_utc = day_end_local.astimezone(UTC)

        for brand in factory.brands.all().order_by("id"):
            summary["brands"] += 1
            account = _resolve_brand_youtube_account(brand)
            if not account:
                logger.info(
                    "[RECONCILE/FULL_SCAN] brand=%s(id=%s): no linked YouTube social account, skipping.",
                    brand.name,
                    brand.id,
                )
                continue
            channel_index = None
            scan_error = None
            scan_credential_label = "social_account_default"
            scan_credential_id = None
            try:
                channel_index = _youtube_day_video_index(account, day_start_utc, day_end_utc)
                logger.info(
                    "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): channel scan OK (default).",
                    brand.name,
                    brand.id,
                    scan_credential_label,
                    scan_credential_id,
                )
            except Exception as exc:
                scan_error = exc
                logger.warning(
                    "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): channel scan failed (default): %s",
                    brand.name,
                    brand.id,
                    scan_credential_label,
                    scan_credential_id,
                    exc,
                )

            # Fallback: scan with each active brand YouTube credential.
            if channel_index is None:
                for yt_cred in _list_ordered_youtube_credentials(brand):
                    if not str(getattr(yt_cred, "refresh_token", "") or "").strip():
                        logger.info(
                            "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): no refresh_token, skipping.",
                            brand.name,
                            brand.id,
                            (yt_cred.label or f"cred#{yt_cred.id}"),
                            yt_cred.id,
                        )
                        continue
                    try:
                        channel_index = _youtube_day_video_index(
                            account,
                            day_start_utc,
                            day_end_utc,
                            youtube_credential=yt_cred,
                        )
                        scan_credential_label = yt_cred.label or f"cred#{yt_cred.id}"
                        scan_credential_id = yt_cred.id
                        scan_error = None
                        logger.info(
                            "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): channel scan OK (fallback).",
                            brand.name,
                            brand.id,
                            scan_credential_label,
                            scan_credential_id,
                        )
                        break
                    except Exception as cred_exc:
                        scan_error = cred_exc
                        logger.warning(
                            "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): channel scan failed (fallback): %s",
                            brand.name,
                            brand.id,
                            (yt_cred.label or f"cred#{yt_cred.id}"),
                            yt_cred.id,
                            cred_exc,
                        )

            if channel_index is None:
                summary["errors"].append(f"brand={brand.id} youtube_scan_error={scan_error}")
                logger.error(
                    "[RECONCILE/FULL_SCAN] brand=%s(id=%s): scan failed for all credentials. error=%s",
                    brand.name,
                    brand.id,
                    scan_error,
                )
                continue

            internal_posts = ScheduledPost.objects.select_related(
                "job",
                "job__brand",
                "auto_cut_corte",
                "auto_cut_corte__analysis",
                "factory_schedule",
                "factory_schedule__brand",
            ).filter(
                Q(factory_schedule__brand=brand)
                | Q(job__brand=brand)
                | Q(auto_cut_corte__analysis__brand=brand),
                scheduled_at__gte=day_start_utc,
                scheduled_at__lte=day_end_utc,
                status__in=["PENDING", "POSTING", "DONE"],
            ).order_by("scheduled_at", "id")

            brand_checked = 0
            brand_confirmed = 0
            brand_still_scheduled = 0
            brand_removed_missing = 0
            brand_skipped_no_external_id = 0
            internal_video_ids = set()
            for post in internal_posts:
                platform = _first_youtube_platform(post.platforms)
                if not platform:
                    continue
                video_id = str((post.external_ids or {}).get(platform) or "")
                if not video_id:
                    summary["skipped_no_external_id"] += 1
                    brand_skipped_no_external_id += 1
                    continue
                summary["checked"] += 1
                brand_checked += 1
                internal_video_ids.add(video_id)
                yt_item = channel_index.get(video_id)
                if not yt_item:
                    _remove_schedule_records_missing_on_youtube(post, "full_scan_not_found")
                    summary["removed_missing"] += 1
                    brand_removed_missing += 1
                    continue

                publish_at_raw = yt_item.get("publish_at") or ""
                publish_at = parse_datetime(publish_at_raw) if publish_at_raw else None
                if publish_at and timezone.is_naive(publish_at):
                    publish_at = timezone.make_aware(publish_at, timezone.get_current_timezone())
                if publish_at and publish_at > timezone.now():
                    mark_still_scheduled(
                        post,
                        publish_at_raw=publish_at_raw,
                        note="Agendado no YouTube (full scan).",
                    )
                    summary["still_scheduled"] += 1
                    brand_still_scheduled += 1
                else:
                    mark_posted(
                        post,
                        platform=platform,
                        external_video_id=video_id,
                        log_metadata={"youtube_verify": {"full_scan": True, **yt_item}},
                    )
                    summary["confirmed"] += 1
                    brand_confirmed += 1

            channel_only_count = max(0, len(channel_index.keys() - internal_video_ids))
            summary["channel_only_videos"] += channel_only_count
            logger.info(
                "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): scan result "
                "checked=%s confirmed=%s still_scheduled=%s removed_missing=%s skipped_no_external_id=%s channel_only_videos=%s",
                brand.name,
                brand.id,
                scan_credential_label,
                scan_credential_id,
                brand_checked,
                brand_confirmed,
                brand_still_scheduled,
                brand_removed_missing,
                brand_skipped_no_external_id,
                channel_only_count,
            )

    return summary


def _run_post_to_platforms(scheduled_post_id: int) -> dict:
    """
    Posting logic (direct call or via task).
    Do not call post_to_platforms_task.apply() from inside another task (deadlock).
    """
    resultado = preflight(scheduled_post_id)
    if isinstance(resultado, EarlyExit):
        return resultado.payload

    post = resultado.post
    brand = resultado.brand
    job_obj = resultado.job
    video_path = resultado.video_path
    correlation_id = resultado.correlation_id
    current_attempt = resultado.current_attempt
    _timer = resultado.timer
    _brand_id = brand.id if brand else None
    # Upload Post targets (TIKTOK, X, INSTAGRAM, YOUTUBE) — same list later passed to the API.
    upload_post_platforms = _build_upload_post_platforms(brand, post) if brand else []
    _post_platforms = list(post.platforms or [])
    log_event(
        logger,
        event="publish_started",
        correlation_id=correlation_id,
        scheduled_post_id=post.id,
        brand_id=_brand_id,
        platform="youtube",
        status="started",
        attempt_number=current_attempt,
        post_platforms=_post_platforms,
        upload_post_platforms=list(upload_post_platforms),
    )
    publish_attempts_total.inc()

    # Factory pause: does not stop content generation, only holds scheduling/posting.
    if brand and getattr(brand, "factory_id", None):
        try:
            if brand.factory and brand.factory.scheduling_paused:
                next_check_at = timezone.now() + timedelta(minutes=5)
                expired_result = _fail_expired_factory_slot(
                    post,
                    correlation_id=correlation_id,
                    brand_id=_brand_id,
                    current_attempt=current_attempt,
                    duration_ms=_timer.elapsed_ms(),
                    reason="A factory permaneceu pausada até além do horário do slot.",
                    check_time=next_check_at,
                )
                if expired_result is not None:
                    return expired_result
                post.status = "PENDING"
                post.scheduled_at = next_check_at
                post.error = "Agendamento da factory pausado. Aguardando retomada."
                post.save(update_fields=["status", "scheduled_at", "error"])
                _sync_factory_posting_schedule(post)
                return {"skipped": "factory scheduling paused"}
        except Exception:
            logger.exception("Failed to check factory pause for ScheduledPost=%s", post.id)

    errors = []
    warnings = []
    retryable_errors = []
    external_ids = dict(post.external_ids or {})
    if external_ids.get("upload_post_skip_after_unknown_no_id"):
        upload_post_platforms = []
    if external_ids.get("upload_post_youtube_terminal_failure"):
        upload_post_platforms = [p for p in upload_post_platforms if p != "YOUTUBE"]
    upload_fingerprint = ""
    social_account_changed = False
    upload_post_youtube_ok = bool(external_ids.get("youtube_via_upload_post"))
    # File hash for deduplication per channel/platform.
    try:
        hasher = hashlib.sha256()
        with open(video_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                hasher.update(chunk)
        upload_fingerprint = hasher.hexdigest()
    except Exception:
        upload_fingerprint = ""
    if not upload_fingerprint:
        upload_fingerprint = str(post.upload_fingerprint or "").strip()
    if not upload_fingerprint:
        errors.append("idempotency: upload_fingerprint indisponível para proteger a publicação")
    if errors:
        post.status = "FAILED"
        post.error = "; ".join(errors)
        post.upload_fingerprint = upload_fingerprint
        post.external_ids = external_ids
        post.save(update_fields=["status", "error", "upload_fingerprint", "external_ids"])
        try:
            FactoryPostingAttemptLog.objects.create(
                posting_schedule=post.factory_schedule,
                attempt_number=current_attempt,
                started_at=timezone.now(),
                finished_at=timezone.now(),
                result="ERROR",
                error_message=post.error,
                provider_response={},
            )
        except Exception:
            pass
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
            error=post.error,
        )
        _sync_factory_posting_schedule(post)
        return {
            "status": post.status,
            "errors": errors,
            "error": post.error,
            "external_ids": external_ids,
        }

    # Upload-Post (preferred): TikTok, X, Instagram, YouTube when enabled on brand.
    # Short and long. Retry 2x at 10s. Fallback to YouTube API on failure.
    # (upload_post_platforms was built above for logging; mutate in place below.)
    # Long-form above limit (~250MB): Upload-Post rejects; use native YouTube API (resumable upload).
    if upload_post_platforms and "YTB" in (post.platforms or []):
        try:
            file_sz = os.path.getsize(video_path)
        except OSError:
            file_sz = 0
        if file_sz > UPLOAD_POST_LONG_MAX_BYTES and "YOUTUBE" in upload_post_platforms:
            upload_post_platforms = [p for p in upload_post_platforms if p != "YOUTUBE"]
            logger.info(
                "[UploadPost] Long video %.1f MB > limit %.0f MB; YouTube outside Upload-Post (native API)",
                file_sz / (1024 * 1024),
                UPLOAD_POST_LONG_MAX_BYTES / (1024 * 1024),
            )
    if not errors and brand and video_path and upload_post_platforms:
        up = publish_via_upload_post(
            post,
            brand,
            video_path=video_path,
            upload_post_platforms=upload_post_platforms,
            upload_fingerprint=upload_fingerprint,
            external_ids=external_ids,
            correlation_id=correlation_id,
            brand_id=_brand_id,
            current_attempt=current_attempt,
            timer=_timer,
            upload_post_youtube_ok=upload_post_youtube_ok,
        )
        if up.early_return is not None:
            return up.early_return
        errors.extend(up.errors)
        warnings.extend(up.warnings)
        retryable_errors.extend(up.retryable_errors)
        upload_post_youtube_ok = up.upload_post_youtube_ok

    nativo = publish_native_platforms(
        post,
        brand,
        video_path=video_path,
        job=job_obj,
        correlation_id=correlation_id,
        brand_id=_brand_id,
        upload_fingerprint=upload_fingerprint,
        upload_post_youtube_ok=upload_post_youtube_ok,
        external_ids=external_ids,
        upload_post_client_request_id_key=UPLOAD_POST_CLIENT_REQUEST_ID_KEY,
    )
    errors.extend(nativo.errors)
    warnings.extend(nativo.warnings)
    retryable_errors.extend(nativo.retryable_errors)
    social_account_changed = social_account_changed or nativo.social_account_changed

    return finalize_publish(
        FinalizeContext(
            post=post,
            brand=brand,
            errors=errors,
            warnings=warnings,
            retryable_errors=retryable_errors,
            external_ids=external_ids,
            upload_fingerprint=upload_fingerprint,
            social_account_changed=social_account_changed,
            correlation_id=correlation_id,
            brand_id=_brand_id,
            current_attempt=current_attempt,
            timer=_timer,
        )
    )


@shared_task(
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=300,
    time_limit=600,
)
def post_to_platforms_task(scheduled_post_id: int):
    """Publish a ScheduledPost to configured platforms."""
    return _run_post_to_platforms(scheduled_post_id)


def _normalize_media_path(path: str) -> str:
    """Normalize path for comparison (forward slashes, no prefix)."""
    if not path or not path.strip():
        return ""
    return path.replace("\\", "/").strip().lstrip("/")


def _get_referenced_media_paths() -> set:
    """
    Collect all file paths referenced in the database.
    Returns set of paths relative to MEDIA_ROOT, normalized.
    """

    refs = set()
    # AutoCutAnalysis.file
    for name in AutoCutAnalysis.objects.exclude(file="").exclude(file__isnull=True).values_list("file", flat=True):
        if name:
            refs.add(_normalize_media_path(name))
    # AutoCutCorte.file and thumbnail (use .name for correct path)
    for corte in AutoCutCorte.objects.only("file", "thumbnail").iterator():
        if corte.file and getattr(corte.file, "name", None):
            refs.add(_normalize_media_path(corte.file.name))
        if corte.thumbnail and getattr(corte.thumbnail, "name", None):
            refs.add(_normalize_media_path(corte.thumbnail.name))
    # AutoCutReadyChunk.file
    for name in AutoCutReadyChunk.objects.exclude(file="").exclude(file__isnull=True).values_list("file", flat=True):
        if name:
            refs.add(_normalize_media_path(name))
    # RenderOutput.file (exports/)
    for name in RenderOutput.objects.exclude(file="").exclude(file__isnull=True).values_list("file", flat=True):
        if name:
            refs.add(_normalize_media_path(name))
    # SourceVideo.file
    for name in SourceVideo.objects.exclude(file="").exclude(file__isnull=True).values_list("file", flat=True):
        if name:
            refs.add(_normalize_media_path(name))
    # BrandAsset.file
    for name in BrandAsset.objects.exclude(file="").exclude(file__isnull=True).values_list("file", flat=True):
        if name:
            refs.add(_normalize_media_path(name))
    # Cut.file
    for name in Cut.objects.exclude(file="").exclude(file__isnull=True).values_list("file", flat=True):
        if name:
            refs.add(_normalize_media_path(name))
    return refs


def _cleanup_orphan_media_files(dry_run: bool = False, min_age_hours: int = 24) -> dict:
    """
    Remove files under storage/media that have no database row.
    Folders: auto_cuts/sources, auto_cuts/cortes, auto_cuts/thumbnails,
            sources, exports, cuts, brands/assets.
    Files modified in the last `min_age_hours` hours are skipped to avoid
    racing with in-flight uploads/renders that haven't persisted their DB
    row yet.
    If dry_run=True, only list orphans without deleting.
    """
    import time as _time

    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.exists():
        return {"orphans_deleted": 0, "orphans_found": [], "skipped_recent": 0, "errors": []}

    refs = _get_referenced_media_paths()
    mtime_cutoff = _time.time() - (min_age_hours * 3600)
    folders = [
        "auto_cuts/sources",
        "auto_cuts/cortes",
        "auto_cuts/thumbnails",
        "sources",
        "exports",
        "cuts",
        "brands/assets",
    ]
    deleted = 0
    skipped_recent = 0
    orphans_found = []
    errors = []
    for folder in folders:
        folder_path = media_root / folder.replace("/", os.sep)
        if not folder_path.exists() or not folder_path.is_dir():
            continue
        try:
            for f in folder_path.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    rel = str(f.relative_to(media_root)).replace("\\", "/")
                    if rel in refs:
                        continue
                    if f.stat().st_mtime > mtime_cutoff:
                        skipped_recent += 1
                        continue
                    orphans_found.append(rel)
                    if not dry_run:
                        f.unlink()
                        deleted += 1
                        logger.info("[CLEANUP] Orphan removed: %s", rel)
                except Exception as e:
                    errors.append(f"orphan_{f}: {e}")
        except Exception as e:
            errors.append(f"folder_{folder}: {e}")
    return {
        "orphans_deleted": deleted,
        "orphans_found": orphans_found,
        "skipped_recent": skipped_recent,
        "errors": errors,
    }


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=120,
    time_limit=180,
    max_retries=2,
)
def post_youtube_first_comment_task(self, scheduled_post_id: int, video_id: str):
    """
    Posta o primeiro comentário (fixado por padrão para o dono do canal)
    em um vídeo já publicado, após um delay. Rodada via Celery com countdown
    para parecer mais natural.

    Idempotente: se external_ids['first_comment_posted'] já está True, sai.
    """
    from googleapiclient.discovery import build


    if not scheduled_post_id or not video_id:
        return {"skipped": "missing_args"}

    post = (
        ScheduledPost.objects.select_related("social_account", "auto_cut_corte")
        .filter(id=scheduled_post_id)
        .first()
    )
    if not post:
        return {"skipped": "post_not_found", "post_id": scheduled_post_id}

    existing_flag = (post.external_ids or {}).get("first_comment_posted")
    if existing_flag:
        return {"skipped": "already_posted", "post_id": scheduled_post_id}

    account = post.social_account
    if not account:
        return {"skipped": "no_social_account", "post_id": scheduled_post_id}

    yt_cred = None
    cred_id = (post.external_ids or {}).get("youtube_credential_id")
    if cred_id:
        yt_cred = BrandYouTubeCredential.objects.filter(id=cred_id).first()

    try:
        creds = get_credentials(account, youtube_credential=yt_cred)
        youtube = build("youtube", "v3", credentials=creds)
    except Exception as e:
        logger.warning(
            "[YT-COMMENT] Falha ao criar client para post=%s video=%s: %s",
            scheduled_post_id,
            video_id,
            e,
        )
        return {"skipped": "credentials_failed", "post_id": scheduled_post_id}

    publisher = YouTubePublisher()
    publisher._post_pinned_first_comment(youtube, video_id, post)

    post.external_ids = dict(post.external_ids or {})
    post.external_ids["first_comment_posted"] = True
    # ScheduledPost não tem updated_at — incluí-lo aqui impedia a gravação do flag
    # first_comment_posted, quebrando a idempotência prometida na docstring.
    post.save(update_fields=["external_ids"])
    return {"ok": True, "post_id": scheduled_post_id, "video_id": video_id}


@shared_task
@instrument_celery_task
def cleanup_posted_media_task():
    """
    Clean media for already-posted videos to save space.
    Runs every 4 hours (when enabled in beat).
    - Cuts (AutoCutCorte): delete file/thumbnail for posted cuts (not available/scheduled)
    - Job output: delete file for DONE jobs whose posts are complete
    - AutoCutAnalysis: delete original upload video for finished analyses
    - Orphan files: delete files under storage/media with no DB row
    Does not delete Jobs or available/scheduled videos.
    """

    summary = {
        "cortes_cleaned": 0,
        "job_outputs_cleaned": 0,
        "analysis_files_cleaned": 0,
        "orphans_deleted": 0,
        "errors": [],
    }

    # 1) Posted cuts: cut IDs with at least one DONE ScheduledPost
    posted_corte_ids = set(
        ScheduledPost.objects.filter(
            status="DONE",
            auto_cut_corte_id__isnull=False,
        ).values_list("auto_cut_corte_id", flat=True)
    )

    # Exclude cuts not yet posted (inventory: available, scheduled, posting, or failed)
    # Only delete when inventory is POSTED or missing (direct post without factory)
    excluded_inventory = set(
        VideoInventoryItem.objects.filter(
            status__in=["AVAILABLE", "SCHEDULED", "POSTING", "FAILED"],
            auto_cut_corte_id__isnull=False,
        ).values_list("auto_cut_corte_id", flat=True)
    )
    posted_corte_ids -= excluded_inventory

    # Exclude cuts with pending or in-flight posts
    active_corte_ids = set(
        ScheduledPost.objects.filter(
            status__in=["PENDING", "POSTING"],
            auto_cut_corte_id__isnull=False,
        ).values_list("auto_cut_corte_id", flat=True)
    )
    posted_corte_ids -= active_corte_ids

    for corte in AutoCutCorte.objects.filter(id__in=posted_corte_ids).select_related("suggestion"):
        try:
            changed = False
            if corte.file:
                try:
                    fp = Path(corte.file.path) if corte.file.name else None
                except Exception:
                    fp = None
                try:
                    corte.file.delete(save=False)
                except Exception as e:
                    logger.warning("[CLEANUP] Failed to delete cut file %s: %s", corte.id, e)
                else:
                    if fp and fp.exists():
                        try:
                            fp.unlink()
                        except Exception:
                            pass
                    corte.file = None
                    changed = True
                    summary["cortes_cleaned"] += 1
            if getattr(corte, "thumbnail", None) and corte.thumbnail:
                try:
                    tfp = Path(corte.thumbnail.path) if corte.thumbnail.name else None
                except Exception:
                    tfp = None
                try:
                    corte.thumbnail.delete(save=False)
                except Exception as e:
                    logger.warning("[CLEANUP] Failed to delete cut thumbnail %s: %s", corte.id, e)
                else:
                    if tfp and tfp.exists():
                        try:
                            tfp.unlink()
                        except Exception:
                            pass
                    corte.thumbnail = None
                    changed = True
            if changed:
                corte.save(update_fields=["file", "thumbnail"])
        except Exception as e:
            logger.exception("[CLEANUP] Error cleaning cut %s", corte.id)
            summary["errors"].append(f"corte_{corte.id}: {e}")

    # 2) Job output (final video): DONE jobs with all posts complete
    # Requer ≥1 post DONE e nenhum PENDING/POSTING, alinhado à Seção 1 (cuts).
    # Evita deletar o output de Jobs renderizados mas nunca agendados/publicados.
    posted_job_ids = set(
        ScheduledPost.objects.filter(
            status="DONE",
            job_id__isnull=False,
        ).values_list("job_id", flat=True)
    )
    active_job_ids = set(
        ScheduledPost.objects.filter(
            status__in=["PENDING", "POSTING"],
            job_id__isnull=False,
        ).values_list("job_id", flat=True)
    )
    eligible_job_ids = posted_job_ids - active_job_ids
    done_jobs = Job.objects.filter(
        id__in=eligible_job_ids,
        status="DONE",
    ).select_related("output")
    for job in done_jobs:
        try:
            output = job.output
        except Exception:
            output = None
        if not output or not output.file:
            continue
        try:
            fp = Path(output.file.path) if output.file.name else None
            output.file.delete(save=True)
            if fp and fp.exists():
                try:
                    fp.unlink()
                except Exception:
                    pass
            summary["job_outputs_cleaned"] += 1
        except Exception as e:
            logger.warning("[CLEANUP] Failed to delete job output %s: %s", job.id, e)
            summary["errors"].append(f"job_{job.id}: {e}")

    # 3) AutoCutAnalysis: original upload for finished analyses.
    # Alinha critérios com a Seção 1 (cuts): exige ≥1 corte efetivamente publicado
    # (ScheduledPost DONE), sem posts ativos, sem inventário em trânsito
    # (AVAILABLE/SCHEDULED/POSTING/FAILED). Protege também analyses sem cortes
    # e analyses cujos cortes nunca chegaram ao VideoInventoryItem (routing sem
    # factory). Evita perda do fonte em análises concluídas mas não publicadas.
    for analysis in AutoCutAnalysis.objects.filter(status="done"):
        if not analysis.file or not analysis.file.name:
            continue
        corte_ids = list(
            AutoCutCorte.objects.filter(analysis=analysis).values_list("id", flat=True)
        )
        if not corte_ids:
            continue
        has_active_inventory = VideoInventoryItem.objects.filter(
            auto_cut_corte_id__in=corte_ids,
            status__in=["AVAILABLE", "SCHEDULED", "POSTING", "FAILED"],
        ).exists()
        if has_active_inventory:
            continue
        has_active_posts = ScheduledPost.objects.filter(
            auto_cut_corte_id__in=corte_ids,
            status__in=["PENDING", "POSTING"],
        ).exists()
        if has_active_posts:
            continue
        has_done_post = ScheduledPost.objects.filter(
            auto_cut_corte_id__in=corte_ids,
            status="DONE",
        ).exists()
        if not has_done_post:
            continue
        try:
            fp = Path(analysis.file.path) if analysis.file.name else None
            analysis.file.delete(save=False)
            if fp and fp.exists():
                try:
                    fp.unlink()
                except Exception:
                    pass
            analysis.file = None
            analysis.save(update_fields=["file"])
            summary["analysis_files_cleaned"] += 1
        except Exception as e:
            logger.warning("[CLEANUP] Failed to delete analysis file %s: %s", analysis.id, e)
            summary["errors"].append(f"analysis_{analysis.id}: {e}")

    # 4) Orphan files: on disk under storage/media with no DB row
    orphan_result = _cleanup_orphan_media_files()
    summary["orphans_deleted"] = orphan_result["orphans_deleted"]
    summary["errors"].extend(orphan_result.get("errors", []))

    if any(v > 0 for k, v in summary.items() if k != "errors" and isinstance(v, int)):
        logger.info(
            "[CLEANUP] Done: cortes=%s job_outputs=%s analysis_files=%s orphans=%s",
            summary["cortes_cleaned"],
            summary["job_outputs_cleaned"],
            summary["analysis_files_cleaned"],
            summary["orphans_deleted"],
        )
    return summary
