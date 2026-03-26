"""Publisher para YouTube (videos longos e Shorts)."""
import json
import logging
import random
import re
import time
from datetime import UTC, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.utils import timezone
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from apps.brands.models import BrandSocialAccount, BrandYouTubeCredential
from apps.jobs.models import ScheduledPost
from apps.social.publishers.base import BasePublisher
from apps.social.services.youtube_credentials import get_credentials
from apps.social.services.youtube_description import build_youtube_description

logger = logging.getLogger(__name__)

RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
MAX_RETRIES = 10
YOUTUBE_TITLE_MAX_LENGTH = 100


def _sanitize_youtube_title(title: str, fallback: str = "Vídeo") -> str:
    """
    Sanitiza título para a API do YouTube.
    Limite: 100 caracteres. Remove caracteres de controle e garante não vazio.
    Substitui < e > por equivalentes seguros (evita invalidTitle).
    """
    if not title or not isinstance(title, str):
        return fallback
    # Remove caracteres de controle (0x00-0x1F, 0x7F)
    cleaned = "".join(c for c in title if ord(c) >= 32 and ord(c) != 127)
    # Substitui < e > que podem causar invalidTitle em alguns contextos
    cleaned = cleaned.replace("<", " - ").replace(">", " - ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return fallback
    return cleaned[:YOUTUBE_TITLE_MAX_LENGTH] or fallback


class YouTubePublishError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        reason: str = "",
        retriable: bool = False,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.retriable = retriable
        self.retry_after_seconds = retry_after_seconds


class YouTubePublisher(BasePublisher):
    """Upload para YouTube via API videos.insert."""

    def publish(
        self,
        account: BrandSocialAccount,
        video_path: str,
        job=None,
        scheduled_post: ScheduledPost | None = None,
        youtube_credential: BrandYouTubeCredential | None = None,
    ) -> dict:
        token_holder = youtube_credential if youtube_credential is not None else account
        if not token_holder.access_token and not token_holder.refresh_token:
            raise ValueError("Conta sem tokens (OAuth não concluído)")
        creds = get_credentials(account, youtube_credential=youtube_credential)
        youtube = build("youtube", "v3", credentials=creds)
        post = scheduled_post
        fallback_title = ""
        if job is not None:
            fallback_title = getattr(job, "name", "") or f"Vídeo {getattr(job, 'id', '')}".strip()
        if not fallback_title and post and getattr(post, "auto_cut_corte_id", None):
            suggestion = getattr(post.auto_cut_corte, "suggestion", None)
            fallback_title = getattr(suggestion, "title", "") or f"Corte {post.auto_cut_corte_id}"
        raw_title = (post.title if post else "") or fallback_title or "Vídeo"
        title = _sanitize_youtube_title(raw_title, fallback="Vídeo")
        description = self._build_description(post)
        language_data = self._build_language_data(post)
        tags = (post.tags if post else []) or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        privacy = (post.privacy_status if post else "private") or "private"
        if privacy not in ("public", "private", "unlisted"):
            privacy = "private"
        made_for_kids = bool(getattr(account.brand, "youtube_made_for_kids", False))
        publish_at = self._get_publish_at(post)
        # Slots fixos já espaçam as postagens; não verificamos mais intervalo mínimo.
        # Padrão para agendamento futuro no YouTube: privado + publishAt.
        if publish_at:
            privacy = "private"
        snippet = {
            "title": title,
            "description": description[:5000],
            "tags": tags[:500] if tags else [],
            "categoryId": "22",
        }
        snippet.update(language_data)
        body = {
            "snippet": snippet,
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": made_for_kids,
                "embeddable": True,
            },
        }
        if publish_at:
            body["status"]["publishAt"] = publish_at
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )
        try:
            response = self._resumable_upload(request)
        except HttpError as e:
            raise self._http_error_to_publish_error(e) from e
        except (OSError, ConnectionError) as e:
            raise YouTubePublishError(
                f"Erro de rede no upload: {e}",
                retriable=True,
            ) from e
        video_id = response.get("id")
        # Thumbnail longos: enviada em lote após postagem (upload_thumbnails_after_batch_task). Shorts: não enviamos.
        result = {"video_id": video_id, "platform": account.platform}
        if youtube_credential is not None:
            result["youtube_credential_id"] = youtube_credential.id
        return result

    def _build_description(self, post: ScheduledPost | None) -> str:
        """Monta descrição final para YouTube (mesmo formato do download de mídias)."""
        if not post:
            return ""
        if not getattr(post, "auto_cut_corte_id", None):
            return (post.description or "")[:5000]
        corte = getattr(post, "auto_cut_corte", None)
        brand = None
        if getattr(post, "social_account", None):
            brand = getattr(post.social_account, "brand", None)
        if not brand and getattr(post, "job", None):
            brand = getattr(post.job, "brand", None)
        if not brand and getattr(post, "factory_schedule", None):
            brand = getattr(post.factory_schedule, "brand", None)
        return build_youtube_description(
            corte=corte,
            brand=brand,
            title=post.title,
            description_override=post.description,
        )

    THUMBNAIL_MAX_SIZE_BYTES = 2 * 1024 * 1024  # 2MB
    THUMBNAIL_RETRY_DELAY_SEC = 60
    THUMBNAIL_MAX_RETRIES = 2  # 3 tentativas no total

    def upload_thumbnail_for_post(
        self, youtube, video_id: str, post: ScheduledPost
    ) -> bool:
        """
        Envia thumbnail para vídeo YouTube. Usado por upload_thumbnails_after_batch_task.
        Retorna True se enviou, False se não há thumbnail, levanta exceção em erro.
        """
        if not video_id or not getattr(post, "auto_cut_corte_id", None):
            return False
        corte = getattr(post, "auto_cut_corte", None)
        if not corte or not getattr(corte, "thumbnail", None):
            return False
        thumb_path = Path(corte.thumbnail.path)
        if not thumb_path.exists():
            raise YouTubePublishError(
                f"Thumbnail não encontrada: {thumb_path}",
                retriable=False,
            )
        size_bytes = thumb_path.stat().st_size
        if size_bytes > self.THUMBNAIL_MAX_SIZE_BYTES:
            logger.warning(
                "[YT] Thumbnail %.1fMB excede 2MB (corte=%s)",
                size_bytes / (1024 * 1024),
                getattr(corte, "id", "?"),
            )
        for attempt in range(self.THUMBNAIL_MAX_RETRIES + 1):
            media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg", resumable=False)
            try:
                youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
                logger.info("[YT] Thumbnail aplicada (video_id=%s)", video_id)
                return True
            except HttpError as e:
                if attempt < self.THUMBNAIL_MAX_RETRIES:
                    logger.info(
                        "[YT] Thumbnail falhou (tentativa %d/%d), aguardando %ds: %s",
                        attempt + 1,
                        self.THUMBNAIL_MAX_RETRIES + 1,
                        self.THUMBNAIL_RETRY_DELAY_SEC,
                        e,
                    )
                    time.sleep(self.THUMBNAIL_RETRY_DELAY_SEC)
                    continue
                err = self._http_error_to_publish_error(e)
                raise YouTubePublishError(
                    f"Thumbnail falhou após {self.THUMBNAIL_MAX_RETRIES + 1} tentativas: {err}",
                    status_code=err.status_code,
                    reason=err.reason,
                    retriable=err.retriable,
                ) from e
        return False

    def _build_language_data(self, post: ScheduledPost | None) -> dict:
        """
        Define idioma conforme prompt do editor:
        - prompt PT -> português
        - prompt EN -> inglês
        """
        if not post or not getattr(post, "auto_cut_corte_id", None):
            return {}
        corte = getattr(post, "auto_cut_corte", None)
        analysis = getattr(corte, "analysis", None) if corte else None
        if not analysis:
            return {}
        prompt_version = (getattr(analysis, "prompt_version", "") or "").strip().lower()
        if prompt_version.endswith("_en"):
            lang = "en"
        else:
            lang = "pt-BR"
        return {
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        }

    def _get_publish_at(self, post: ScheduledPost | None) -> str | None:
        """
        Agendamento nativo YouTube: usa publishAt quando o horário ainda está no futuro.
        """
        if not post or not getattr(post, "scheduled_at", None):
            return None
        scheduled_at = post.scheduled_at
        if timezone.is_naive(scheduled_at):
            scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())
        # YouTube rejeita publishAt no passado; usa apenas com alguma folga.
        if scheduled_at <= timezone.now() + timedelta(seconds=30):
            return None
        return scheduled_at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _http_error_to_publish_error(self, e: HttpError) -> YouTubePublishError:
        status_code = getattr(getattr(e, "resp", None), "status", None)
        reason = ""
        message = ""
        try:
            payload = json.loads((e.content or b"").decode("utf-8"))
            err = payload.get("error", {})
            message = err.get("message", "") or ""
            details = err.get("errors", []) or []
            if details:
                reason = details[0].get("reason", "") or ""
        except Exception:
            message = str(e)
        retriable = bool(status_code in RETRIABLE_STATUS_CODES or status_code == 429)
        retry_after_seconds = None
        if reason == "quotaExceeded":
            retriable = True
            retry_after_seconds = self._seconds_until_youtube_quota_reset()
        elif reason == "uploadLimitExceeded":
            # Limite de upload do canal (vídeos/dia), não cota da API no Cloud Console.
            retriable = True
            retry_after_seconds = 24 * 3600
        detail_msg = f"HTTP {status_code or '-'}"
        if reason:
            detail_msg += f" reason={reason}"
        if message:
            detail_msg += f" msg={message}"
        if reason == "uploadLimitExceeded":
            detail_msg = (
                "Canal atingiu o limite de uploads do dia (YouTube). "
                "Não é cota da API no Cloud Console. Nova tentativa em 24h."
            )
        return YouTubePublishError(
            detail_msg,
            status_code=status_code,
            reason=reason,
            retriable=retriable,
            retry_after_seconds=retry_after_seconds,
        )

    def _seconds_until_youtube_quota_reset(self) -> int:
        """
        Cota diária do YouTube Data API reinicia na meia-noite do horário do Pacífico.
        Adiciona buffer de 5 min para evitar corrida no reset.
        """
        try:
            now_pt = timezone.now().astimezone(ZoneInfo("America/Los_Angeles"))
            next_reset = (now_pt + timedelta(days=1)).replace(
                hour=0,
                minute=5,
                second=0,
                microsecond=0,
            )
            delay = int((next_reset - now_pt).total_seconds())
            return max(delay, 900)
        except Exception:
            # Fallback seguro: 6h
            return 6 * 60 * 60

    def _resumable_upload(self, request):
        response = None
        retry = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if response is not None:
                    return response
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    retry += 1
                    if retry > MAX_RETRIES:
                        raise
                    time.sleep(random.random() * (2**retry))
                else:
                    raise
            except (OSError, ConnectionError):
                retry += 1
                if retry > MAX_RETRIES:
                    raise
                time.sleep(random.random() * (2**retry))
        raise RuntimeError("Upload falhou sem resposta")
