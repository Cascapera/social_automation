"""
Busca vídeos de canais do YouTube via Data API v3.
Usa YOUTUBE_API_KEY (recomendado) ou credenciais YOUTUBE_CHECK_* com OAuth.
"""
import logging
import os
import re
from urllib.parse import parse_qs, urlparse

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Padrões para extrair handle ou channel ID de URLs
CHANNEL_ID_PATTERN = re.compile(r"(?:youtube\.com/channel/|/channel/)([A-Za-z0-9_-]{24})")


def extract_video_id(url: str) -> str | None:
    """Extrai video_id de URL do YouTube (watch, youtu.be, etc)."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if "youtu.be/" in url:
        part = url.split("youtu.be/")[-1].split("?")[0].split("&")[0].strip()
        return part if part and len(part) == 11 else None
    parsed = urlparse(url)
    if "youtube.com" in (parsed.netloc or ""):
        qs = parse_qs(parsed.query)
        vid = (qs.get("v") or [None])[0]
        return str(vid).strip() if vid and len(str(vid)) == 11 else None
    return None


def register_manual_youtube_success(analysis) -> None:
    """
    After a **manual** analysis finishes successfully (status done), record the YouTube video id
    so `check_and_fetch_new_videos_task` does not pick the same video again.

    Do not call for auto-fetch jobs (``user_id`` is None): those already register
    ``ProcessedYoutubeVideo`` when the job is enqueued.

    Registering only on success allows a failed manual run to leave the video available
    for automatic fetch; the user can re-run the same URL without an early duplicate row.
    """
    if getattr(analysis, "user_id", None) is None:
        return
    youtube_url = (getattr(analysis, "youtube_url", None) or "").strip()
    if not youtube_url:
        return
    vid = extract_video_id(youtube_url)
    if not vid:
        return
    brand = getattr(analysis, "brand", None)
    factory_id = getattr(brand, "factory_id", None) if brand else None
    if not factory_id:
        return
    from apps.brands.models import ProcessedYoutubeVideo

    ProcessedYoutubeVideo.objects.get_or_create(
        factory_id=factory_id,
        youtube_video_id=vid,
        defaults={"source": "manual"},
    )


HANDLE_PATTERN = re.compile(r"(?:youtube\.com/@|youtube\.com/c/|@)([A-Za-z0-9_.-]+)")


def _get_youtube_client():
    """
    Retorna cliente da YouTube Data API v3.
    Usa YOUTUBE_API_KEY (recomendado) ou GOOGLE_API_KEY.
    Para criar: Google Cloud Console > APIs & Services > Credentials > Create API Key.
    Habilite a API "YouTube Data API v3" no projeto.
    """
    api_key = (os.getenv("YOUTUBE_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if api_key:
        return build("youtube", "v3", developerKey=api_key)

    # Fallback: OAuth com YOUTUBE_CHECK_* via FactoryYouTubeCheckCredential
    from apps.brands.models import FactoryYouTubeCheckCredential

    fcred = (
        FactoryYouTubeCheckCredential.objects.exclude(refresh_token="")
        .select_related("factory")
        .first()
    )
    if fcred:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials

            check_id = (os.getenv("YOUTUBE_CHECK_CLIENT_ID") or "").strip()
            check_secret = (os.getenv("YOUTUBE_CHECK_CLIENT_SECRET") or "").strip()
            if check_id and check_secret:
                from apps.social.services.youtube_oauth import YOUTUBE_SCOPES

                creds = Credentials(
                    token=fcred.access_token or None,
                    refresh_token=fcred.refresh_token or None,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=check_id,
                    client_secret=check_secret,
                    scopes=YOUTUBE_SCOPES,
                    expiry=fcred.expires_at,
                )
                if creds.expired or not creds.token:
                    creds.refresh(Request())
                    fcred.access_token = creds.token
                    fcred.expires_at = creds.expiry
                    fcred.save(update_fields=["access_token", "expires_at", "updated_at"])
                return build("youtube", "v3", credentials=creds)
        except Exception as e:
            logger.warning("[YOUTUBE_FETCH] OAuth FactoryYouTubeCheckCredential falhou: %s", e)

    return None


def parse_channel_identifier(url_or_handle: str) -> tuple[str | None, str | None]:
    """
    Extrai channel_id ou handle de URL/handle.
    Retorna (channel_id, handle). Um deles pode ser None.
    """
    url_or_handle = (url_or_handle or "").strip()
    if not url_or_handle:
        return None, None

    # Já é um ID de canal (24 caracteres típicos)
    if re.match(r"^UC[A-Za-z0-9_-]{22}$", url_or_handle):
        return url_or_handle, None

    # URL
    if "youtube.com" in url_or_handle or "youtu.be" in url_or_handle:
        m = CHANNEL_ID_PATTERN.search(url_or_handle)
        if m:
            return m.group(1), None
        m = HANDLE_PATTERN.search(url_or_handle)
        if m:
            return None, m.group(1).lstrip("@")

    # Apenas handle (@nome ou nome)
    if url_or_handle.startswith("@"):
        return None, url_or_handle[1:]
    if not url_or_handle.startswith("http"):
        return None, url_or_handle

    return None, None


def resolve_channel_id(youtube, channel_id: str | None, handle: str | None) -> str | None:
    """Resolve channel_id a partir de handle ou retorna channel_id se já tiver."""
    if channel_id:
        return channel_id
    if not handle:
        return None
    try:
        resp = youtube.channels().list(
            part="id,snippet",
            forHandle=handle if handle.startswith("@") else f"@{handle}",
            maxResults=1,
        ).execute()
        items = (resp or {}).get("items") or []
        if items:
            return str(items[0].get("id") or "")
    except HttpError as e:
        logger.warning("[YOUTUBE_FETCH] Erro ao resolver handle %s: %s", handle, e)
    return None


def _parse_iso8601_duration(duration_str: str) -> float:
    """Converte duração ISO 8601 (ex: PT1H30M45S) para segundos."""
    if not duration_str or not isinstance(duration_str, str):
        return 0.0
    import re
    total = 0.0
    # PT1H2M30S -> H=1, M=2, S=30
    for unit, mult in [("H", 3600), ("M", 60), ("S", 1)]:
        m = re.search(rf"(\d+(?:\.\d+)?){unit}", duration_str)
        if m:
            total += float(m.group(1)) * mult
    return total


def fetch_latest_videos(
    channel_id: str,
    *,
    max_results: int = 10,
    exclude_live: bool = True,
    min_hours_since_publish: float | None = None,
    max_hours_since_publish: float | None = None,
    min_duration_minutes: float | None = None,
    min_views: int | None = None,
) -> list[dict]:
    """
    Busca os vídeos mais recentes de um canal.
    Retorna lista de dicts com: video_id, title, published_at, url, live_broadcast_content.
    min_hours_since_publish: filtra vídeos publicados há menos de N horas.
    max_hours_since_publish: filtra vídeos publicados há mais de N horas (tema esfriou).
    min_duration_minutes: filtra vídeos com duração menor que N min (evita cortes que os canais postam).
    min_views: filtra vídeos com menos de N visualizações (ex: 10000).
    """
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    youtube = _get_youtube_client()
    if not youtube:
        logger.warning("[YOUTUBE_FETCH] API não configurada (YOUTUBE_API_KEY ou YOUTUBE_CHECK_*)")
        return []

    try:
        resp = youtube.search().list(
            part="id,snippet",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=min(max_results, 50),
        ).execute()
    except HttpError as e:
        logger.warning("[YOUTUBE_FETCH] Erro ao buscar vídeos do canal %s: %s", channel_id, e)
        return []

    items = (resp or {}).get("items") or []
    result = []
    for item in items:
        vid = item.get("id") or {}
        video_id = str(vid.get("videoId") or "").strip()
        if not video_id:
            continue
        snippet = item.get("snippet") or {}
        live = str(snippet.get("liveBroadcastContent") or "").lower()
        if exclude_live and live == "live":
            continue
        published_at_raw = snippet.get("publishedAt") or ""
        if min_hours_since_publish is not None and min_hours_since_publish > 0 and published_at_raw:
            try:
                published = parse_datetime(published_at_raw)
                if published and published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published:
                    now = timezone.now()
                    if now.tzinfo is None:
                        now = now.replace(tzinfo=timezone.utc)
                    delta = (now - published).total_seconds() / 3600
                    if min_hours_since_publish is not None and delta < min_hours_since_publish:
                        continue
                    if max_hours_since_publish is not None and max_hours_since_publish > 0 and delta > max_hours_since_publish:
                        continue
            except (ValueError, TypeError):
                pass
        result.append({
            "video_id": video_id,
            "title": str(snippet.get("title") or ""),
            "published_at": published_at_raw,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "live_broadcast_content": live,
        })

    needs_videos_list = (min_duration_minutes is not None and min_duration_minutes > 0) or (
        min_views is not None and min_views > 0
    )
    if needs_videos_list and result:
        video_ids = [r["video_id"] for r in result]
        try:
            parts = ["contentDetails", "statistics"]
            resp = youtube.videos().list(
                part=",".join(parts),
                id=",".join(video_ids[:50]),
                maxResults=min(len(video_ids), 50),
            ).execute()
            duration_by_id = {}
            views_by_id = {}
            for item in (resp.get("items") or []):
                vid = item.get("id")
                content = item.get("contentDetails") or {}
                dur_str = content.get("duration") or ""
                duration_by_id[vid] = _parse_iso8601_duration(dur_str)
                stats = item.get("statistics") or {}
                try:
                    views_by_id[vid] = int(stats.get("viewCount") or 0)
                except (ValueError, TypeError):
                    views_by_id[vid] = 0
            if min_duration_minutes is not None and min_duration_minutes > 0:
                min_seconds = min_duration_minutes * 60
                result = [r for r in result if duration_by_id.get(r["video_id"], 0) >= min_seconds]
            if min_views is not None and min_views > 0:
                result = [r for r in result if views_by_id.get(r["video_id"], 0) >= min_views]
        except HttpError as e:
            logger.warning("[YOUTUBE_FETCH] Erro ao buscar detalhes dos vídeos: %s", e)

    return result


def get_channel_info(channel_id: str) -> dict | None:
    """Retorna snippet do canal (title, etc)."""
    youtube = _get_youtube_client()
    if not youtube:
        return None
    try:
        resp = youtube.channels().list(
            part="snippet",
            id=channel_id,
            maxResults=1,
        ).execute()
        items = (resp or {}).get("items") or []
        if items:
            return items[0].get("snippet") or {}
    except HttpError as e:
        logger.warning("[YOUTUBE_FETCH] Erro ao buscar canal %s: %s", channel_id, e)
    return None
