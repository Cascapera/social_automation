"""Tasks de postagem em redes sociais."""
import os
import hashlib
import logging
from datetime import datetime, timedelta, time
from datetime import timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from celery import shared_task
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from apps.brands.models import Brand, Factory, BrandSocialAccount, BrandYouTubeCredential
from apps.jobs.models import (
    ScheduledPost,
    FactoryPostingSchedule,
    FactoryPostingAttemptLog,
    FactoryScheduleRun,
    PostedVideoLog,
    VideoInventoryItem,
    Job,
    RenderOutput,
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


def _youtube_channel_key_and_interval(post) -> tuple[str | None, int]:
    """
    Para posts YouTube, retorna (channel_key, min_interval_seconds) para serialização.
    channel_key identifica o canal; min_interval é o intervalo mínimo em segundos.
    Para não-YouTube retorna (None, 0).
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
    # Usa defaults: shorts 60 min, longos 180 min (slots fixos já espaçam; intervalo só para fila de envio)
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

    # Usar o mesmo OAuth client que emitiu o token (brand/global). Check client causa unauthorized_client.
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

    # Mesmo OAuth client que emitiu o token (brand/global); check client causa unauthorized_client.
    creds = get_credentials(
        account,
        youtube_credential=youtube_credential,
        use_check_client=False,
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
            # YouTube: upload com sucesso = vídeo já está no canal (público ou agendado).
            # Marcamos como POSTED para ir para "Vídeos Postados" e não rechecar (economiza cota).
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
    Atualiza o ScheduledPost para DONE para sair da lista "aguardando" e ir para "postados".
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


UPLOAD_INTERVAL_SECONDS = 60  # 1 vídeo por minuto na fila de envio
THUMBNAIL_BATCH_DELAY_SEC = 120  # Buffer após último vídeo antes de subir capas


@shared_task
def upload_thumbnails_after_batch_task(brand_id: int, post_ids: list[int] | None = None):
    """
    Sobe capas dos vídeos YouTube de uma brand após todos os vídeos terem sido publicados.
    Chamado após o último vídeo da brand (check_scheduled_posts_task agenda com countdown).
    Retry: 60s entre tentativas, máx 2 retries (3 tentativas no total) por vídeo.
    post_ids: IDs dos posts da batch (opcional; se vazio, usa todos DONE da brand).
    Para Shorts (YT): só envia se factory.send_thumbnail ativo. Para longos (YTB): sempre envia.
    """
    try:
        brand = Brand.objects.select_related("factory").get(id=brand_id)
    except Brand.DoesNotExist:
        logger.warning("[THUMB] Brand %s não encontrada para upload de capas", brand_id)
        return {"brand_id": brand_id, "uploaded": 0, "skipped": 0, "errors": 0}

    factory = getattr(brand, "factory", None)
    send_thumbnail_shorts = factory and getattr(factory, "send_thumbnail", False)

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
    for p in posts:
        b = _resolve_post_target_brand(p)
        if b and b.id == brand_id:
            if getattr(p.auto_cut_corte, "thumbnail", None):
                platforms = p.platforms or []
                is_short = "YT" in platforms and "YTB" not in platforms
                if is_short and not send_thumbnail_shorts:
                    continue
                video_id = str((p.external_ids or {}).get("YT") or (p.external_ids or {}).get("YTB") or "")
                if video_id:
                    to_upload.append((p, video_id))

    if not to_upload:
        return {"brand_id": brand_id, "uploaded": 0, "skipped": 0, "errors": 0}

    account = BrandSocialAccount.objects.filter(
        brand=brand,
        platform__in=["YT", "YTB"],
    ).order_by("id").first()
    creds_list = _list_ordered_youtube_credentials(brand)
    cred = creds_list[0] if creds_list else None
    if not account and not cred:
        logger.warning("[THUMB] Brand %s sem conta/credencial YouTube", brand_id)
        return {"brand_id": brand_id, "uploaded": 0, "skipped": len(to_upload), "errors": 0}

    from apps.social.publishers import get_publisher
    from apps.social.services.youtube_credentials import get_credentials
    from googleapiclient.discovery import build

    publisher = get_publisher("YT")
    if not publisher:
        return {"brand_id": brand_id, "uploaded": 0, "skipped": len(to_upload), "errors": 0}

    token_holder = cred if cred else account
    if not token_holder.access_token and not token_holder.refresh_token:
        logger.warning("[THUMB] Brand %s: conta/credencial sem tokens", brand_id)
        return {"brand_id": brand_id, "uploaded": 0, "skipped": len(to_upload), "errors": 0}

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
            logger.warning("[THUMB] Falha ao subir capa video_id=%s: %s", video_id, e)

    return {"brand_id": brand_id, "uploaded": uploaded, "skipped": len(to_upload) - uploaded - errors, "errors": errors}


@shared_task
def check_scheduled_posts_task():
    """
    Roda a cada minuto via Beat.
    - Pega posts PENDING (scheduled_at <= now ou YouTube antecipado).
    - Ordena brand por brand: processa toda a brand 1, depois brand 2, etc.
    - Enfileira com 1 vídeo por minuto (countdown 0, 60, 120...) para aliviar a API.
    """
    now = timezone.now()
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
    # Brand por brand, 1 vídeo por minuto
    for i, p in enumerate(posts):
        countdown = i * UPLOAD_INTERVAL_SECONDS
        post_to_platforms_task.apply_async(args=[p.id], countdown=countdown)

    # Thumbnails: agendadas após último vídeo de cada brand (upload_thumbnails_after_batch_task
    # é chamada ao concluir cada post; aqui garantimos o batch para posts do check).
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
        factory = getattr(brand, "factory", None)
        send_thumb = factory and getattr(factory, "send_thumbnail", False)
        if is_short and not send_thumb:
            continue
        brand_to_last_index[brand.id] = i
        brand_to_post_ids.setdefault(brand.id, []).append(p.id)
    for brand_id, last_i in brand_to_last_index.items():
        countdown = (last_i + 1) * UPLOAD_INTERVAL_SECONDS + THUMBNAIL_BATCH_DELAY_SEC
        post_ids = brand_to_post_ids.get(brand_id, [])
        upload_thumbnails_after_batch_task.apply_async(
            args=[brand_id],
            kwargs={"post_ids": post_ids},
            countdown=countdown,
        )

    return {
        "checked_due": due_posts.count(),
        "queued": len(post_ids_list),
    }


@shared_task
def generate_daily_factory_schedules_task():
    """
    A cada 30 min: para cada factory ativa, se já passou do horário fixo de agendamento
    (daily_schedule_start_time, padrão 19:00) e ainda não foi gerada a agenda do DIA SEGUINTE, gera.
    Os vídeos são agendados para o dia seguinte (ex: às 19h de 10/03 agenda para 11/03 8h, 9h, 10h...).
    """
    now = timezone.now()
    created_total = 0
    generated = 0
    for factory in Factory.objects.filter(is_active=True, scheduling_paused=False).order_by("id"):
        tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
        now_local = now.astimezone(tz)
        # Agenda sempre para o DIA SEGUINTE
        target_date = now_local.date() + timedelta(days=1)
        start_time = getattr(factory, "daily_schedule_start_time", None) or time(19, 0)
        if now_local.time() < start_time:
            continue
        if FactoryScheduleRun.objects.filter(factory=factory, run_date=target_date).exists():
            continue
        try:
            result = generate_daily_schedule_for_factory(
                factory, now_utc=now, target_date=target_date
            )
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
    # Só PENDING e POSTING: DONE já foi confirmado, rechecar gastaria cota à toa.
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
            # Confirmado no YouTube (já publicado ou agendado para o futuro): marca como POSTED
            # e não recheca mais, economizando cota da API.
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
            "factory_schedule",
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
            last_exception = None
            last_is_retriable = False
            last_reason = ""
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
                    last_exception = e
                    last_is_retriable = is_retriable
                    last_reason = reason
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
                    # Qualquer outro erro: salva na credencial e tenta a próxima
                    yt_cred.last_error = f"{reason or 'erro'}: {msg}"[:500]
                    yt_cred.save(update_fields=["last_error", "updated_at"])
                    continue

            if published:
                continue
            # Todas as credenciais falharam: agenda retry ou marca erro
            if last_exception is not None:
                if last_is_retriable:
                    retryable_errors.append(
                        {
                            "message": f"{platform}: {last_exception}",
                            "retry_after_seconds": getattr(last_exception, "retry_after_seconds", None),
                            "reason": last_reason,
                        }
                    )
                else:
                    errors.append(f"{platform}: {last_exception}")
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
        has_upload_limit_exceeded = any(
            (item.get("reason") or "").strip() == "uploadLimitExceeded"
            for item in retryable_errors
        )
        has_min_interval_not_reached = any(
            (item.get("reason") or "").strip() == "minIntervalNotReached"
            for item in retryable_errors
        )
        next_retry = int(post.retry_count or 0) + 1
        should_not_consume_attempt = (
            has_quota_exceeded or has_upload_limit_exceeded or has_min_interval_not_reached
        )
        # 1 retentativa para erros de upload/título; erros de token/cota não contam
        if not should_not_consume_attempt and next_retry > 1:
            errors.extend([item["message"] for item in retryable_errors])
        else:
            requested_delays = [
                int(item["retry_after_seconds"])
                for item in retryable_errors
                if item.get("retry_after_seconds")
            ]
            # quotaExceeded / uploadLimitExceeded: aguarda reset (sem falha definitiva).
            if has_quota_exceeded:
                delay = max([3600] + requested_delays)
            elif has_upload_limit_exceeded:
                delay = max([24 * 3600] + requested_delays)
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
                post.error = f"Falha temporária. 1 tentativa automática em {delay}s. Reagende manualmente se persistir. {msg}"
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
    # Upload-Post (TikTok, X, Instagram): apenas para Shorts (YT). Longos (YTB) não vão para Upload-Post.
    upload_post_platforms = []
    is_short = "YT" in (post.platforms or []) and "YTB" not in (post.platforms or [])
    if not errors and brand and video_path and is_short:
        if getattr(brand, "upload_post_tiktok_enabled", False):
            upload_post_platforms.append("TIKTOK")
        if getattr(brand, "upload_post_x_enabled", False):
            upload_post_platforms.append("X")
        if getattr(brand, "upload_post_instagram_enabled", False):
            upload_post_platforms.append("INSTAGRAM")
    if not upload_post_platforms and not errors and brand:
        logger.info(
            "[UploadPost] Pulado: brand_%s platforms=%s is_short=%s tiktok=%s x=%s insta=%s",
            brand.id,
            post.platforms,
            is_short,
            getattr(brand, "upload_post_tiktok_enabled", False),
            getattr(brand, "upload_post_x_enabled", False),
            getattr(brand, "upload_post_instagram_enabled", False),
        )
    if upload_post_platforms:
        try:
            from apps.social.publishers.upload_post import publish_to_upload_post

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
                desc_by_platform[p] = f"{title}\n\n{extra}".strip() if extra else title
            tz_name = "America/Sao_Paulo"
            if getattr(brand, "factory_id", None) and getattr(brand, "factory", None):
                tz_name = (brand.factory.timezone or "").strip() or tz_name
            result = publish_to_upload_post(
                video_path=video_path,
                brand_id=brand.id,
                platforms=upload_post_platforms,
                title=title,
                description_by_platform=desc_by_platform,
                scheduled_at=post.scheduled_at,
                timezone_name=tz_name,
            )
            if result.get("success"):
                logger.info("[UploadPost] Enviado para %s (brand_%s)", upload_post_platforms, brand.id)
            else:
                warnings.append(f"Upload-Post: {result.get('error', 'erro')}")
        except Exception as e:
            logger.warning("[UploadPost] Falha: %s", e)
            warnings.append(f"Upload-Post: {e}")

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

    # Para posts manuais (retry, run_scheduled_posts_now): agenda upload de capa
    if post.status == "DONE" and _platforms_are_youtube_only(post.platforms):
        brand = _resolve_post_target_brand(post)
        platforms = post.platforms or []
        is_short = "YT" in platforms and "YTB" not in platforms
        factory = getattr(brand, "factory", None) if brand else None
        send_thumb = factory and getattr(factory, "send_thumbnail", False)
        qualifies = (is_short and send_thumb) or (not is_short)
        if qualifies and brand and post.auto_cut_corte_id and getattr(post.auto_cut_corte, "thumbnail", None):
            video_id = str((post.external_ids or {}).get("YT") or (post.external_ids or {}).get("YTB") or "")
            if video_id:
                upload_thumbnails_after_batch_task.apply_async(
                    args=[brand.id],
                    kwargs={"post_ids": [post.id]},
                    countdown=THUMBNAIL_BATCH_DELAY_SEC,
                )
    return {"status": post.status, "errors": errors}


@shared_task
def cleanup_posted_media_task():
    """
    Limpa mídias de vídeos já postados para economizar espaço.
    Roda a cada 4 horas.
    - Cortes (AutoCutCorte): apaga file e thumbnail de cortes postados (não disponíveis/agendados)
    - Job output: apaga arquivo de jobs finalizados cujos posts já foram concluídos
    - AutoCutAnalysis: apaga vídeo original (upload) de análises finalizadas
    Não apaga Jobs, nem vídeos disponíveis ou agendados.
    """
    from apps.auto_cuts.models import AutoCutCorte, AutoCutAnalysis

    summary = {"cortes_cleaned": 0, "job_outputs_cleaned": 0, "analysis_files_cleaned": 0, "errors": []}

    # 1) Cortes postados: IDs de cortes que têm pelo menos um ScheduledPost DONE
    posted_corte_ids = set(
        ScheduledPost.objects.filter(
            status="DONE",
            auto_cut_corte_id__isnull=False,
        ).values_list("auto_cut_corte_id", flat=True)
    )

    # Excluir cortes que ainda estão disponíveis ou agendados no inventário
    excluded_inventory = set(
        VideoInventoryItem.objects.filter(
            status__in=["AVAILABLE", "SCHEDULED"],
            auto_cut_corte_id__isnull=False,
        ).values_list("auto_cut_corte_id", flat=True)
    )
    posted_corte_ids -= excluded_inventory

    # Excluir cortes que têm posts pendentes ou em postagem
    active_corte_ids = set(
        ScheduledPost.objects.filter(
            status__in=["PENDING", "POSTING"],
            auto_cut_corte_id__isnull=False,
        ).values_list("auto_cut_corte_id", flat=True)
    )
    posted_corte_ids -= active_corte_ids

    for corte in AutoCutCorte.objects.filter(id__in=posted_corte_ids):
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
                    logger.warning("[CLEANUP] Falha ao deletar file do corte %s: %s", corte.id, e)
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
                    logger.warning("[CLEANUP] Falha ao deletar thumbnail do corte %s: %s", corte.id, e)
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
            logger.exception("[CLEANUP] Erro ao limpar corte %s", corte.id)
            summary["errors"].append(f"corte_{corte.id}: {e}")

    # 2) Job output (vídeo final): jobs DONE cujos posts já foram concluídos
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
            logger.warning("[CLEANUP] Falha ao deletar output do job %s: %s", job.id, e)
            summary["errors"].append(f"job_{job.id}: {e}")

    # 3) AutoCutAnalysis: vídeo original (upload direto) de análises finalizadas
    for analysis in AutoCutAnalysis.objects.filter(status="done"):
        if not analysis.file or not analysis.file.name:
            continue
        # Só apaga se não houver cortes ainda disponíveis/agendados desta análise
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
            logger.warning("[CLEANUP] Falha ao deletar file da análise %s: %s", analysis.id, e)
            summary["errors"].append(f"analysis_{analysis.id}: {e}")

    if any(v > 0 for k, v in summary.items() if k != "errors" and isinstance(v, int)):
        logger.info(
            "[CLEANUP] Concluído: cortes=%s job_outputs=%s analysis_files=%s",
            summary["cortes_cleaned"],
            summary["job_outputs_cleaned"],
            summary["analysis_files_cleaned"],
        )
    return summary
