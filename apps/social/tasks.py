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

from apps.brands.models import Brand, BrandSocialAccount, BrandYouTubeCredential, Factory
from apps.common.metrics import (
    publish_attempts_total,
    publish_duration_ms,
    publish_failures_total,
    publish_quota_exhaustion_attempts_total,
    publish_reconciliation_duration_ms,
    publish_reconciliation_failures_total,
    publish_reconciliation_runs_total,
)
from apps.common.task_observability import instrument_celery_task
from apps.jobs.logging_utils import (
    Timer,
    log_event,
    new_correlation_id,
    resolve_scheduled_post_correlation_id,
)
from apps.jobs.models import (
    FactoryPostingAttemptLog,
    FactoryPostingSchedule,
    FactoryScheduleRun,
    Job,
    PostedVideoLog,
    RenderOutput,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory
from apps.social.services.idempotency import (
    acquire_idempotency_key,
    get_existing_idempotency_result,
    mark_idempotency_failed,
    mark_idempotency_success,
)

logger = logging.getLogger(__name__)
YOUTUBE_PLATFORM_CODES = {"YT", "YTB"}
# Upload-Post rejects very large files (~250MB); longer videos above that use native YouTube API only.
UPLOAD_POST_LONG_MAX_BYTES = 250 * 1024 * 1024
BATCH_LIMIT_PER_TICK = 20
YOUTUBE_VERIFY_GRACE_SECONDS = 600
YOUTUBE_CHECK_CLIENT_ENABLED = bool(
    (os.getenv("YOUTUBE_CHECK_CLIENT_ID") or "").strip()
    and (os.getenv("YOUTUBE_CHECK_CLIENT_SECRET") or "").strip()
)


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
        from apps.brands.models import BrandSocialAccount

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
    from apps.brands.models import BrandSocialAccount

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
    from apps.brands.models import BrandSocialAccount

    return (
        BrandSocialAccount.objects.filter(brand=brand, platform__in=["YTB", "YT"])
        .order_by("id")
        .first()
    )


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
    max_pages = max(1, min(12, int(os.getenv("YOUTUBE_FULL_SCAN_MAX_PAGES", "4") or "4")))
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


def _sync_factory_posting_schedule(post: ScheduledPost) -> None:
    schedule = FactoryPostingSchedule.objects.filter(scheduled_post=post).select_related(
        "inventory_item", "factory", "brand"
    ).first()
    if not schedule:
        return
    item = schedule.inventory_item
    now = timezone.now()
    is_youtube = _platforms_are_youtube_only(post.platforms)
    if post.status == "DONE":
        if is_youtube:
            # YouTube: successful upload means video is on channel (public or scheduled).
            # Mark POSTED for Posted Videos list and skip re-check (saves quota).
            schedule.status = "DONE"
            schedule.attempt_count = int(post.retry_count or 0)
            schedule.next_retry_at = None
            schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
            item.status = "POSTED"
            item.posted_at = post.posted_at or now
            item.scheduled_for = post.scheduled_at
            item.last_error = ""
            item.attempt_count = int(post.retry_count or 0)
            item.save(update_fields=["status", "posted_at", "scheduled_for", "last_error", "attempt_count", "updated_at"])
            external_video_id = ""
            for code in (post.platforms or []):
                external_video_id = str((post.external_ids or {}).get(code) or "")
                if external_video_id:
                    break
            if external_video_id and not PostedVideoLog.objects.filter(
                inventory_item=item,
                external_platform=((post.platforms or ["YT"])[0] if post.platforms else "YT"),
                external_video_id=external_video_id,
            ).exists():
                PostedVideoLog.objects.create(
                    factory=schedule.factory,
                    brand=schedule.brand,
                    inventory_item=item,
                    external_platform=((post.platforms or ["YT"])[0] if post.platforms else "YT"),
                    external_video_id=external_video_id,
                    posted_at=post.posted_at or now,
                    metadata_snapshot={
                        "scheduled_post_id": post.id,
                        "platforms": post.platforms or [],
                        "external_ids": post.external_ids or {},
                    },
                )
            return
        schedule.status = "DONE"
        schedule.attempt_count = int(post.retry_count or 0)
        schedule.next_retry_at = None
        schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
        item.status = "POSTED"
        item.posted_at = post.posted_at or now
        item.scheduled_for = post.scheduled_at  # fills schedule time once YouTube confirmed
        item.last_error = ""
        item.attempt_count = int(post.retry_count or 0)
        item.save(update_fields=["status", "posted_at", "scheduled_for", "last_error", "attempt_count", "updated_at"])
        external_video_id = ""
        for code in (post.platforms or []):
            external_video_id = str((post.external_ids or {}).get(code) or "")
            if external_video_id:
                break
        PostedVideoLog.objects.create(
            factory=schedule.factory,
            brand=schedule.brand,
            inventory_item=item,
            external_platform=((post.platforms or ["YT"])[0] if post.platforms else "YT"),
            external_video_id=external_video_id,
            posted_at=post.posted_at or now,
            metadata_snapshot={
                "scheduled_post_id": post.id,
                "platforms": post.platforms or [],
                "external_ids": post.external_ids or {},
            },
        )
        return
    if post.status == "FAILED":
        quota_attempts = int(getattr(post, "youtube_quota_retry_count", 0) or 0)
        attempts = int(post.retry_count or 0) + quota_attempts
        schedule.status = "FAILED"
        schedule.attempt_count = attempts
        schedule.next_retry_at = None
        schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
        item.status = "AVAILABLE"
        item.scheduled_for = None
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


def _mark_factory_posting_verified(post: ScheduledPost, *, platform: str, external_video_id: str, metadata: dict | None = None) -> None:
    """
    Mark schedule/inventory as confirmed on the platform.
    Set ScheduledPost to DONE to leave waiting list and move to posted.
    """
    schedule = FactoryPostingSchedule.objects.filter(scheduled_post=post).select_related(
        "inventory_item", "factory", "brand"
    ).first()
    if not schedule:
        return
    item = schedule.inventory_item
    now = timezone.now()
    post.status = "DONE"
    post.posted_at = post.posted_at or now
    post.error = ""
    post.save(update_fields=["status", "posted_at", "error", "updated_at"])
    schedule.status = "DONE"
    schedule.attempt_count = int(post.retry_count or 0)
    schedule.next_retry_at = None
    schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
    item.status = "POSTED"
    item.posted_at = post.posted_at or now
    item.scheduled_for = post.scheduled_at
    item.last_error = ""
    item.attempt_count = int(post.retry_count or 0)
    item.save(update_fields=["status", "posted_at", "scheduled_for", "last_error", "attempt_count", "updated_at"])
    if not PostedVideoLog.objects.filter(
        inventory_item=item,
        external_platform=platform,
        external_video_id=external_video_id,
    ).exists():
        PostedVideoLog.objects.create(
            factory=schedule.factory,
            brand=schedule.brand,
            inventory_item=item,
            external_platform=platform,
            external_video_id=external_video_id,
            posted_at=post.posted_at or now,
            metadata_snapshot={
                "scheduled_post_id": post.id,
                "platforms": post.platforms or [],
                "external_ids": post.external_ids or {},
                "youtube_verify": metadata or {},
            },
        )


def _mark_factory_posting_still_scheduled(post: ScheduledPost, *, publish_at_raw: str | None, note: str = "") -> None:
    """
    Keep internal status as scheduled on channel (not published yet), without confirming POSTED.
    """
    schedule = FactoryPostingSchedule.objects.filter(scheduled_post=post).select_related(
        "inventory_item"
    ).first()
    if not schedule:
        return
    item = schedule.inventory_item
    next_check = timezone.now() + timedelta(minutes=15)
    publish_at = parse_datetime(str(publish_at_raw or "")) if publish_at_raw else None
    if publish_at:
        if timezone.is_naive(publish_at):
            publish_at = timezone.make_aware(publish_at, timezone.get_current_timezone())
        # Re-check shortly after actual publish time on channel.
        next_check = max(next_check, publish_at + timedelta(minutes=5))

    schedule.status = "PLANNED"
    schedule.next_retry_at = next_check
    schedule.save(update_fields=["status", "next_retry_at", "updated_at"])
    item.status = "SCHEDULED"
    item.last_error = note or "Agendado no YouTube. Aguardando publicação no canal."
    item.save(update_fields=["status", "last_error", "updated_at"])


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


UPLOAD_INTERVAL_SECONDS = 60  # One video per minute on send queue
THUMBNAIL_BATCH_DELAY_SEC = 120  # Buffer after last video before thumbnail uploads
UPLOAD_POST_RETRY_COUNT = 2  # Max retries for Upload Post
UPLOAD_POST_RETRY_DELAY_SEC = 10  # Seconds between retries
# YouTube API quotaExceeded: no máximo 2 retries (3 tentativas no total); depois FAILED e inventário AVAILABLE.
YOUTUBE_QUOTA_MAX_RETRIES = 2
IDEMPOTENCY_IN_PROGRESS_DELAY_SEC = 60


@shared_task
def upload_thumbnails_after_batch_task(brand_id: int, post_ids: list[int] | None = None):
    """
    Upload YouTube thumbnails for a brand after all videos in batch are published.
    Called after last video for brand (check_scheduled_posts_task schedules with countdown).
    Retry: 60s between attempts, max 2 retries (3 attempts total) per video.
    post_ids: batch post IDs (optional; if empty, all DONE for brand).
    Shorts (YT): do not send cover to YouTube (local generation still; saves quota).
    Long-form (YTB): send cover when cut has thumbnail.
    Skips posts with external_ids.youtube_via_upload_post (YouTube entregue pelo Upload Post;
    capa não deve ser enviada pela API nativa — evita quota e chamadas redundantes).
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

    from apps.social.publishers import get_publisher
    from apps.social.services.youtube_credentials import get_credentials

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


@shared_task
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
    - Picks PENDING posts (scheduled_at <= now or early YouTube).
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
    # YouTube early upload: private upload with publishAt on YouTube.
    future_candidates = ScheduledPost.objects.filter(
        status="PENDING",
        scheduled_at__gt=now + timedelta(seconds=30),
    ).filter(has_origin).select_related("job", "job__brand", "social_account")
    post_ids = {post.id for post in due_posts}
    for post in future_candidates:
        if _platforms_are_youtube_only(post.platforms):
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


@shared_task(bind=True)
def generate_daily_factory_schedules_task(self):
    """
    Every 30 min: for each active factory, if past fixed schedule time
    (daily_schedule_start_time, default 19:00) and next day's schedule not yet generated, generate it.
    Videos are scheduled for the following day (e.g. at 19:00 on 10/03 schedules for 11/03 8:00, 9:00...).
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
        # Always schedule for the NEXT day
        target_date = now_local.date() + timedelta(days=1)
        start_time = getattr(factory, "daily_schedule_start_time", None) or time(19, 0)
        if now_local.time() < start_time:
            continue
        if FactoryScheduleRun.objects.filter(factory=factory, run_date=target_date).exists():
            continue

        factory_timer = Timer()
        try:
            result = generate_daily_schedule_for_factory(
                factory,
                now_utc=now,
                target_date=target_date,
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
                _mark_factory_posting_verified(
                    post,
                    platform=platform,
                    external_video_id=video_id,
                    metadata=verify_data,
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
            _mark_factory_posting_still_scheduled(
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
                    _mark_factory_posting_still_scheduled(
                        post,
                        publish_at_raw=publish_at_raw,
                        note="Agendado no YouTube (full scan).",
                    )
                    summary["still_scheduled"] += 1
                    brand_still_scheduled += 1
                else:
                    _mark_factory_posting_verified(
                        post,
                        platform=platform,
                        external_video_id=video_id,
                        metadata={"full_scan": True, **yt_item},
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
    _timer = Timer()

    try:
        post = ScheduledPost.objects.select_related(
            "job",
            "job__brand",
            "social_account",
            "auto_cut_corte",
            "auto_cut_corte__analysis",
            "auto_cut_corte__suggestion",
            "factory_schedule",
        ).get(id=scheduled_post_id)
    except ScheduledPost.DoesNotExist:
        return {"error": "ScheduledPost não encontrado"}

    correlation_id = resolve_scheduled_post_correlation_id(post)

    claimed = ScheduledPost.objects.filter(id=post.id, status="PENDING").update(status="POSTING")
    if not claimed:
        return {"skipped": "status não é PENDING"}
    current_attempt = int(post.retry_count or 0) + 1
    post.status = "POSTING"
    brand = None
    video_path = ""
    job_obj = post.job

    if post.job_id:
        brand = post.job.brand
        if not brand:
            post.status = "FAILED"
            post.error = "Job sem marca"
            post.save(update_fields=["status", "error"])
            return {"error": "Job sem marca"}
        output = post.job.output
        if not output or not output.file:
            post.status = "FAILED"
            post.error = "Job sem vídeo final"
            post.save(update_fields=["status", "error"])
            return {"error": "Job sem vídeo final"}
        video_path = output.file.path
    elif post.auto_cut_corte_id:
        corte = post.auto_cut_corte
        brand = corte.analysis.brand if corte and corte.analysis_id else None
        if not brand:
            post.status = "FAILED"
            post.error = "AutoCut sem marca"
            post.save(update_fields=["status", "error"])
            return {"error": "AutoCut sem marca"}
        if not corte.file:
            post.status = "FAILED"
            post.error = "AutoCut sem vídeo finalizado"
            post.save(update_fields=["status", "error"])
            return {"error": "AutoCut sem vídeo finalizado"}
        video_path = corte.file.path
    else:
        post.status = "FAILED"
        post.error = "ScheduledPost sem origem (job/corte)"
        post.save(update_fields=["status", "error"])
        return {"error": "ScheduledPost sem origem"}

    # In factory context, prefer schedule destination brand for account/credential.
    target_brand = _resolve_post_target_brand(post)
    if target_brand:
        brand = target_brand

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
                post.status = "PENDING"
                post.scheduled_at = timezone.now() + timedelta(minutes=5)
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
        import time as _time

        from apps.social.publishers.upload_post import (
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
            upload_post_result_keys = {
                "TIKTOK": "tiktok",
                "X": "x",
                "INSTAGRAM": "instagram",
                "YOUTUBE": "youtube",
            }
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
                    )
                    if result.get("success"):
                        up_success = True
                        request_id = str(result.get("request_id") or "").strip()
                        logger.info(
                            "[UploadPost] Posting confirmation received (id %s)",
                            request_id or "ok",
                        )
                        if request_id:
                            external_ids["upload_post_request_id"] = request_id
                        up_results = (result.get("data") or {}).get("results") or {}
                        for up_platform in upload_post_platforms_to_execute:
                            logical_platform = _logical_upload_post_platform(post, up_platform)
                            plat_data = up_results.get(upload_post_result_keys[up_platform]) or {}
                            external_ids_delta: dict[str, str | bool] = {}
                            if request_id:
                                external_ids_delta["upload_post_request_id"] = request_id
                            if plat_data.get("success"):
                                vid = plat_data.get("video_id") or plat_data.get("publish_id")
                                if vid:
                                    external_ids[logical_platform] = str(vid)
                                    external_ids_delta[logical_platform] = str(vid)
                            if logical_platform in YOUTUBE_PLATFORM_CODES and (
                                request_id or external_ids_delta.get(logical_platform)
                            ):
                                upload_post_youtube_ok = True
                                external_ids["youtube_via_upload_post"] = True
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
                        logger.warning("[UploadPost] Failed after %s attempts: %s", UPLOAD_POST_RETRY_COUNT + 1, last_up_error)
                        if "YOUTUBE" in upload_post_platforms_to_execute:
                            logger.info("[UploadPost] Falling back to YouTube API")
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
                        logger.warning("[UploadPost] Failed after %s attempts: %s", UPLOAD_POST_RETRY_COUNT + 1, last_up_error)
                        if "YOUTUBE" in upload_post_platforms_to_execute:
                            logger.info("[UploadPost] Falling back to YouTube API")

        if not up_success and last_up_error:
            for idempotency_key in upload_post_keys_by_platform.values():
                mark_idempotency_failed(key=idempotency_key, error_message=last_up_error)
            if "YOUTUBE" not in upload_post_platforms_to_execute:
                warnings.append(f"Upload-Post: {last_up_error}")

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
                            "remove_external_ids": ["youtube_via_upload_post"],
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
                    "remove_external_ids": ["youtube_via_upload_post"] if platform in ("YT", "YTB") else [],
                    "provider_response": result or {},
                },
            )
        except Exception as e:
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
            else:
                delay = max([300] + requested_delays)
            msg = " ; ".join([item["message"] for item in retryable_errors])
            post.status = "PENDING"
            if should_not_consume_attempt:
                post.retry_count = int(post.retry_count or 0)
            else:
                post.retry_count = next_retry
            post.scheduled_at = timezone.now() + timedelta(seconds=delay)
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
        if warnings:
            post.error = "; ".join(warnings)
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


@shared_task
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
    from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte
    from apps.brands.models import BrandAsset
    from apps.cuts.models import Cut
    from apps.mediahub.models import SourceVideo

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


def _cleanup_orphan_media_files(dry_run: bool = False) -> dict:
    """
    Remove files under storage/media that have no database row.
    Folders: auto_cuts/sources, auto_cuts/cortes, auto_cuts/thumbnails,
            sources, exports, cuts, brands/assets.
    If dry_run=True, only list orphans without deleting.
    """
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.exists():
        return {"orphans_deleted": 0, "orphans_found": [], "errors": []}

    refs = _get_referenced_media_paths()
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
                    if rel not in refs:
                        orphans_found.append(rel)
                        if not dry_run:
                            f.unlink()
                            deleted += 1
                            logger.info("[CLEANUP] Orphan removed: %s", rel)
                except Exception as e:
                    errors.append(f"orphan_{f}: {e}")
        except Exception as e:
            errors.append(f"folder_{folder}: {e}")
    return {"orphans_deleted": deleted, "orphans_found": orphans_found, "errors": errors}


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
    from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte

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
    done_jobs = Job.objects.filter(status="DONE").select_related("output")
    for job in done_jobs:
        try:
            output = job.output
        except Exception:
            output = None
        if not output or not output.file:
            continue
        has_pending = ScheduledPost.objects.filter(
            job_id=job.id,
            status__in=["PENDING", "POSTING"],
        ).exists()
        if has_pending:
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

    # 3) AutoCutAnalysis: original upload video for finished analyses
    for analysis in AutoCutAnalysis.objects.filter(status="done"):
        if not analysis.file or not analysis.file.name:
            continue
        # Only delete if no cuts from this analysis are still available/scheduled
        cortes_from_analysis = AutoCutCorte.objects.filter(analysis=analysis).values_list("id", flat=True)
        has_available_or_scheduled = VideoInventoryItem.objects.filter(
            auto_cut_corte_id__in=cortes_from_analysis,
            status__in=["AVAILABLE", "SCHEDULED"],
        ).exists()
        if has_available_or_scheduled:
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
