"""Tasks de postagem em redes sociais."""
import os
import hashlib
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from celery import shared_task
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from apps.brands.models import Factory, BrandYouTubeCredential
from apps.jobs.models import (
    ScheduledPost,
    FactoryPostingSchedule,
    FactoryPostingAttemptLog,
    PostedVideoLog,
)
from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory

logger = logging.getLogger(__name__)
YOUTUBE_PLATFORM_CODES = {"YT", "YTB"}
BATCH_LIMIT_PER_TICK = 20
YOUTUBE_VERIFY_GRACE_SECONDS = 600
YOUTUBE_CHECK_CLIENT_ENABLED = bool(
    (os.getenv("YOUTUBE_CHECK_CLIENT_ID") or "").strip()
    and (os.getenv("YOUTUBE_CHECK_CLIENT_SECRET") or "").strip()
)


def _cleanup_local_media_if_possible(post: ScheduledPost) -> None:
    """
    Remove arquivos locais após postagem concluída para economizar armazenamento.
    Só limpa quando não há outros agendamentos ativos para a mesma origem.
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
        logger.exception("Falha ao limpar mídias locais do ScheduledPost=%s", post.id)


def _platforms_are_youtube_only(platforms) -> bool:
    codes = {str(code).strip().upper() for code in (platforms or []) if str(code).strip()}
    return bool(codes) and codes.issubset(YOUTUBE_PLATFORM_CODES)


def _first_youtube_platform(platforms) -> str | None:
    for code in (platforms or []):
        normalized = str(code).strip().upper()
        if normalized in YOUTUBE_PLATFORM_CODES:
            return normalized
    return None


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
    Resolve a brand efetiva de publicação.
    Prioriza a brand da FactoryPostingSchedule (destino roteado da Factory).
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
    Confirma se o vídeo existe no canal autenticado.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from apps.social.services.youtube_credentials import get_credentials

    creds = get_credentials(
        account,
        youtube_credential=youtube_credential,
        use_check_client=YOUTUBE_CHECK_CLIENT_ENABLED,
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
    Confirma existência no YouTube tentando conta padrão e credenciais da brand em fallback.
    """
    # 1) tenta fluxo padrão da conta social vinculada
    exists, data = _youtube_video_exists_on_channel(account, video_id)
    if exists:
        return True, data

    # 2) em erro de auth/token, tenta credenciais YouTube da brand
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
    Só remove da agenda quando temos evidência de ausência real no YouTube.
    Erros de auth/rede/temporários NÃO removem.
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
    Indexa vídeos do canal no dia (publicados e agendados).
    """
    from googleapiclient.discovery import build
    from apps.social.services.youtube_credentials import get_credentials

    creds = get_credentials(
        account,
        youtube_credential=youtube_credential,
        use_check_client=YOUTUBE_CHECK_CLIENT_ENABLED,
    )
    youtube = build("youtube", "v3", credentials=creds)
    # Descobre playlist de uploads da conta autenticada.
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
    # Limite conservador para evitar consumo excessivo de cota.
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

    # Carrega detalhes de status/publishAt em lotes.
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

            # No dia: considera publishAt (quando existir) ou data de upload.
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
            # YouTube: DONE aqui significa upload retornou sucesso, mas ainda
            # aguardamos confirmação na plataforma para concluir o ciclo.
            schedule.status = "POSTING"
            schedule.attempt_count = int(post.retry_count or 0)
            schedule.next_retry_at = timezone.now() + timedelta(seconds=YOUTUBE_VERIFY_GRACE_SECONDS)
            schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
            item.status = "POSTING"
            item.last_error = "Aguardando confirmação de agendamento no YouTube."
            item.attempt_count = int(post.retry_count or 0)
            item.save(update_fields=["status", "last_error", "attempt_count", "updated_at"])
            return
        schedule.status = "DONE"
        schedule.attempt_count = int(post.retry_count or 0)
        schedule.next_retry_at = None
        schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
        item.status = "POSTED"
        item.posted_at = post.posted_at or now
        item.scheduled_for = post.scheduled_at  # preenche "Agendado" só quando YouTube confirmou
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
        schedule.status = "FAILED"
        schedule.attempt_count = int(post.retry_count or 0)
        schedule.next_retry_at = None
        schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
        item.status = "AVAILABLE"
        item.scheduled_for = None
        item.last_error = post.error or ""
        item.attempt_count = int(post.retry_count or 0)
        item.save(update_fields=["status", "scheduled_for", "last_error", "attempt_count", "updated_at"])
        return
    if post.status == "PENDING" and int(post.retry_count or 0) > 0:
        schedule.status = "PLANNED"
        schedule.attempt_count = int(post.retry_count or 0)
        schedule.next_retry_at = post.scheduled_at
        schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])
        item.status = "SCHEDULED"
        item.last_error = post.error or ""
        item.attempt_count = int(post.retry_count or 0)
        item.save(update_fields=["status", "last_error", "attempt_count", "updated_at"])


def _mark_factory_posting_verified(post: ScheduledPost, *, platform: str, external_video_id: str, metadata: dict | None = None) -> None:
    """
    Marca agenda/inventário como efetivamente confirmado na plataforma.
    """
    schedule = FactoryPostingSchedule.objects.filter(scheduled_post=post).select_related(
        "inventory_item", "factory", "brand"
    ).first()
    if not schedule:
        return
    item = schedule.inventory_item
    now = timezone.now()
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
    Mantém status interno como agendado no canal (ainda não publicado), sem confirmar POSTED.
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
        # Rechecagem logo após o horário real de publicação no canal.
        next_check = max(next_check, publish_at + timedelta(minutes=5))

    schedule.status = "PLANNED"
    schedule.next_retry_at = next_check
    schedule.save(update_fields=["status", "next_retry_at", "updated_at"])
    item.status = "SCHEDULED"
    item.last_error = note or "Agendado no YouTube. Aguardando publicação no canal."
    item.save(update_fields=["status", "last_error", "updated_at"])


def _remove_schedule_records_missing_on_youtube(post: ScheduledPost, reason: str) -> None:
    """
    Remove da agenda interna quando o item não existe no YouTube.
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


@shared_task
def check_scheduled_posts_task():
    """
    Roda a cada minuto via Beat.
    - Fluxo padrão: publica PENDING quando scheduled_at <= now.
    - YouTube-only (YT/YTB): antecipa upload mesmo com data futura para usar publishAt.
    """
    now = timezone.now()
    now_local = timezone.localtime(now)
    if now_local.hour == 11:
        generate_daily_factory_schedules_task.delay()

    due_posts = ScheduledPost.objects.filter(
        status="PENDING",
        scheduled_at__lte=now,
    ).select_related("job", "job__brand", "social_account").order_by(
        "social_account__brand_id",
        "scheduled_at",
        "id",
    )[:BATCH_LIMIT_PER_TICK]
    # Antecipação para YouTube: sobe privado e deixa publishAt no YouTube.
    future_candidates = ScheduledPost.objects.filter(
        status="PENDING",
        scheduled_at__gt=now + timedelta(seconds=30),
    ).select_related("job", "job__brand", "social_account")
    post_ids = {post.id for post in due_posts}
    for post in future_candidates:
        if _platforms_are_youtube_only(post.platforms):
            post_ids.add(post.id)
    for post_id in sorted(post_ids):
        post_to_platforms_task.delay(post_id)
    # Reconciliação pós-agendamento YouTube (não bloqueia fluxo principal).
    try:
        reconcile_youtube_schedules_task.delay()
    except Exception:
        logger.exception("Falha ao enfileirar reconciliação YouTube.")
    return {
        "checked_due": due_posts.count(),
        "queued": len(post_ids),
        "queued_future_youtube": max(0, len(post_ids) - due_posts.count()),
    }


@shared_task
def generate_daily_factory_schedules_task():
    """
    Gera agenda diária das factories ativas (executa no horário local 11:00).
    É idempotente por factory/dia.
    """
    now = timezone.now()
    created_total = 0
    generated = 0
    for factory in Factory.objects.filter(is_active=True, scheduling_paused=False).order_by("id"):
        try:
            result = generate_daily_schedule_for_factory(factory, now_utc=now)
            if result.get("created", 0):
                generated += 1
                created_total += int(result.get("created", 0))
        except Exception:
            logger.exception("Falha ao gerar agenda diária da factory=%s", factory.id)
    return {"generated_factories": generated, "created_posts": created_total}


@shared_task
def reconcile_youtube_schedules_task():
    """
    Verifica se os uploads/agendamentos YouTube realmente existem no canal.
    Se faltar, re-enfileira o agendamento sem travar os demais.
    Só limpa mídia local após confirmação real.
    """
    now = timezone.now()
    window_start = now - timedelta(days=1)
    window_end = now + timedelta(days=1)
    candidates = (
        ScheduledPost.objects.select_related(
            "job",
            "job__brand",
            "social_account",
            "auto_cut_corte",
            "auto_cut_corte__analysis",
        )
        .filter(
            status__in=["PENDING", "POSTING", "DONE"],
            scheduled_at__gte=window_start,
            scheduled_at__lte=window_end,
        )
        .order_by("scheduled_at", "id")[:500]
    )
    checked = 0
    confirmed = 0
    requeued = 0
    failed_no_media = 0
    skipped = 0
    removed_missing = 0
    still_scheduled = 0
    for post in candidates:
        platform = _first_youtube_platform(post.platforms)
        if not platform:
            continue
        video_id = str((post.external_ids or {}).get(platform) or "")
        if not video_id:
            # Sem id externo não há como reconciliar no canal.
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
            # Se ainda está agendado no canal para o futuro, mantém PLANNED.
            if publish_at and publish_at > now:
                still_scheduled += 1
                _mark_factory_posting_still_scheduled(
                    post,
                    publish_at_raw=publish_at_raw,
                    note="Agendado no YouTube e aguardando horário de publicação.",
                )
            else:
                confirmed += 1
                _mark_factory_posting_verified(
                    post,
                    platform=platform,
                    external_video_id=video_id,
                    metadata=verify_data,
                )
                _cleanup_local_media_if_possible(post)
            continue
        # Só remove quando há evidência de ausência real.
        if _should_remove_missing_by_verify_error(verify_data):
            _remove_schedule_records_missing_on_youtube(post, verify_data.get("error", "unknown"))
            removed_missing += 1
            continue
        # Erro temporário (auth/rede/etc): mantém agendado e revalida no próximo ciclo.
        skipped += 1
        _mark_factory_posting_still_scheduled(
            post,
            publish_at_raw=None,
            note=f"Falha temporária na confirmação YouTube: {verify_data.get('error', 'unknown')}",
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
def reconcile_youtube_full_scan_task(factory_id: int | None = None, day_iso: str | None = None):
    """
    Full scan diário por canal:
    - varre vídeos agendados/postados no YouTube no dia
    - reconcilia agenda interna
    - remove registros internos ausentes no YouTube
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
                summary["errors"].append(f"day_iso inválido: {day_iso}")
                continue

        day_start_local = timezone.make_aware(datetime.combine(target_day, datetime.min.time()), factory_tz)
        day_end_local = day_start_local + timedelta(days=1) - timedelta(microseconds=1)
        day_start_utc = day_start_local.astimezone(dt_timezone.utc)
        day_end_utc = day_end_local.astimezone(dt_timezone.utc)

        for brand in factory.brands.all().order_by("id"):
            summary["brands"] += 1
            account = _resolve_brand_youtube_account(brand)
            if not account:
                logger.info(
                    "[RECONCILE/FULL_SCAN] brand=%s(id=%s): sem conta social YouTube vinculada, pulando.",
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
                    "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): scan do canal OK (default).",
                    brand.name,
                    brand.id,
                    scan_credential_label,
                    scan_credential_id,
                )
            except Exception as exc:
                scan_error = exc
                logger.warning(
                    "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): falha no scan do canal (default): %s",
                    brand.name,
                    brand.id,
                    scan_credential_label,
                    scan_credential_id,
                    exc,
                )

            # Fallback: tenta varrer com cada credencial YouTube ativa da brand.
            if channel_index is None:
                for yt_cred in _list_ordered_youtube_credentials(brand):
                    if not str(getattr(yt_cred, "refresh_token", "") or "").strip():
                        logger.info(
                            "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): sem refresh_token, pulando.",
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
                            "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): scan do canal OK (fallback).",
                            brand.name,
                            brand.id,
                            scan_credential_label,
                            scan_credential_id,
                        )
                        break
                    except Exception as cred_exc:
                        scan_error = cred_exc
                        logger.warning(
                            "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): falha no scan do canal (fallback): %s",
                            brand.name,
                            brand.id,
                            (yt_cred.label or f"cred#{yt_cred.id}"),
                            yt_cred.id,
                            cred_exc,
                        )

            if channel_index is None:
                summary["errors"].append(f"brand={brand.id} youtube_scan_error={scan_error}")
                logger.error(
                    "[RECONCILE/FULL_SCAN] brand=%s(id=%s): scan falhou em todas as credenciais. erro=%s",
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
                "[RECONCILE/FULL_SCAN] brand=%s(id=%s) cred=%s(id=%s): resultado scan "
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


@shared_task(bind=True)
def post_to_platforms_task(self, scheduled_post_id: int):
    """Publica um ScheduledPost nas plataformas configuradas."""
    try:
        post = ScheduledPost.objects.select_related(
            "job",
            "job__brand",
            "social_account",
            "auto_cut_corte",
            "auto_cut_corte__analysis",
            "auto_cut_corte__suggestion",
        ).get(id=scheduled_post_id)
    except ScheduledPost.DoesNotExist:
        return {"error": "ScheduledPost não encontrado"}
    if post.status != "PENDING":
        return {"skipped": "status não é PENDING"}
    current_attempt = int(post.retry_count or 0) + 1
    post.status = "POSTING"
    post.save(update_fields=["status"])
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

    # Em contexto factory, prioriza brand de destino da agenda para conta/credencial de publicação.
    target_brand = _resolve_post_target_brand(post)
    if target_brand:
        brand = target_brand

    # Pausa por factory: não interrompe geração de conteúdo, apenas segura agendamento/publicação.
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
            logger.exception("Falha ao verificar pause da factory no ScheduledPost=%s", post.id)

    errors = []
    warnings = []
    retryable_errors = []
    external_ids = dict(post.external_ids or {})
    upload_fingerprint = ""
    social_account_changed = False
    # Hash do arquivo para deduplicação por canal/plataforma.
    try:
        hasher = hashlib.sha256()
        with open(video_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                hasher.update(chunk)
        upload_fingerprint = hasher.hexdigest()
    except Exception:
        upload_fingerprint = ""

    for platform in post.platforms:
        account = post.social_account
        if not account or account.platform != platform:
            from apps.brands.models import BrandSocialAccount

            # YouTube Shorts (YT) e YouTube longos (YTB) usam o mesmo OAuth.
            # Se não houver conta no código exato, tenta o código alternativo.
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
        if not post.social_account_id:
            post.social_account = account
            social_account_changed = True
        # Deduplicação extra: evita upload duplicado acidental para mesmo canal/plataforma.
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
                errors.append(f"{platform}: upload duplicado detectado (mesmo arquivo e canal)")
                continue
        from apps.social.publishers import get_publisher

        publisher = get_publisher(platform)
        if not publisher:
            errors.append(f"{platform}: publisher não implementado")
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
                continue

            published = False
            hard_failed = False
            for yt_cred in available_credentials:
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
                    warning = (result or {}).get("warning")
                    if warning:
                        warnings.append(f"{platform}: {warning}")
                    if yt_cred.quota_exceeded_until or yt_cred.last_error:
                        yt_cred.quota_exceeded_until = None
                        yt_cred.last_error = ""
                        yt_cred.save(update_fields=["quota_exceeded_until", "last_error", "updated_at"])
                    published = True
                    break
                except Exception as e:
                    reason = str(getattr(e, "reason", "") or "").strip()
                    is_retriable = bool(getattr(e, "retriable", False))
                    msg = str(e)
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
                    if is_retriable:
                        retryable_errors.append(
                            {
                                "message": f"{platform}: {e}",
                                "retry_after_seconds": getattr(e, "retry_after_seconds", None),
                                "reason": reason,
                            }
                        )
                    else:
                        errors.append(f"{platform}: {e}")
                    hard_failed = True
                    break

            if published or hard_failed:
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
            warning = (result or {}).get("warning")
            if warning:
                warnings.append(f"{platform}: {warning}")
        except Exception as e:
            if getattr(e, "retriable", False):
                retryable_errors.append(
                    {
                        "message": f"{platform}: {e}",
                        "retry_after_seconds": getattr(e, "retry_after_seconds", None),
                        "reason": str(getattr(e, "reason", "") or ""),
                    }
                )
            else:
                errors.append(f"{platform}: {e}")
    if retryable_errors and not errors:
        has_quota_exceeded = any(
            (item.get("reason") or "").strip() == "quotaExceeded"
            for item in retryable_errors
        )
        has_min_interval_not_reached = any(
            (item.get("reason") or "").strip() == "minIntervalNotReached"
            for item in retryable_errors
        )
        next_retry = int(post.retry_count or 0) + 1
        should_not_consume_attempt = has_quota_exceeded or has_min_interval_not_reached
        if not should_not_consume_attempt and next_retry > 3:
            errors.extend([item["message"] for item in retryable_errors])
        else:
            requested_delays = [
                int(item["retry_after_seconds"])
                for item in retryable_errors
                if item.get("retry_after_seconds")
            ]
            # quotaExceeded: aguarda reset da cota diária (sem falha definitiva).
            if has_quota_exceeded:
                delay = max([3600] + requested_delays)
            elif has_min_interval_not_reached:
                delay = max([60] + requested_delays)
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
            else:
                post.error = f"Falha temporária (tentativa {next_retry}/3). Próxima tentativa em {delay}s. {msg}"
            post.upload_fingerprint = upload_fingerprint
            post.external_ids = external_ids
            retry_fields = [
                "status",
                "retry_count",
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
        post.posted_at = timezone.now()
        if warnings:
            post.error = "; ".join(warnings)
    post.upload_fingerprint = upload_fingerprint
    post.external_ids = external_ids
    update_fields = ["status", "error", "posted_at", "upload_fingerprint", "external_ids", "retry_count"]
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
    _sync_factory_posting_schedule(post)
    return {"status": post.status, "errors": errors}
