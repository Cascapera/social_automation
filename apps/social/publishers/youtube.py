"""Publisher para YouTube (vídeos longos e Shorts)."""
import random
import time
import json
from datetime import timedelta, timezone as dt_timezone
from pathlib import Path

from django.utils import timezone
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from apps.brands.models import BrandSocialAccount
from apps.jobs.models import ScheduledPost
from apps.social.publishers.base import BasePublisher
from apps.social.services.youtube_credentials import get_credentials

RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
MAX_RETRIES = 10


class YouTubePublishError(Exception):
    def __init__(self, message: str, status_code: int | None = None, reason: str = "", retriable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.retriable = retriable


class YouTubePublisher(BasePublisher):
    """Upload para YouTube via API videos.insert."""

    def publish(
        self,
        account: BrandSocialAccount,
        video_path: str,
        job=None,
        scheduled_post: ScheduledPost | None = None,
    ) -> dict:
        if not account.access_token and not account.refresh_token:
            raise ValueError("Conta sem tokens (OAuth não concluído)")
        creds = get_credentials(account)
        youtube = build("youtube", "v3", credentials=creds)
        post = scheduled_post
        fallback_title = ""
        if job is not None:
            fallback_title = getattr(job, "name", "") or f"Vídeo {getattr(job, 'id', '')}".strip()
        if not fallback_title and post and getattr(post, "auto_cut_corte_id", None):
            suggestion = getattr(post.auto_cut_corte, "suggestion", None)
            fallback_title = getattr(suggestion, "title", "") or f"Corte {post.auto_cut_corte_id}"
        title = (post.title if post else "") or fallback_title or "Vídeo"
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
        # Padrão para agendamento futuro no YouTube: privado + publishAt.
        if publish_at:
            privacy = "private"
        snippet = {
            "title": title[:200],
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
        # Thumbnail é opcional: se existir no corte, tentamos aplicar.
        thumb_warning = ""
        try:
            self._set_thumbnail_if_present(youtube, video_id, post)
        except YouTubePublishError as e:
            thumb_warning = str(e)
        result = {"video_id": video_id, "platform": account.platform}
        if thumb_warning:
            result["warning"] = thumb_warning
        return result

    def _build_description(self, post: ScheduledPost | None) -> str:
        """Monta descrição final para YouTube."""
        if not post:
            return ""
        # Fluxo padrão (jobs manuais)
        if not getattr(post, "auto_cut_corte_id", None):
            return (post.description or "")[:5000]

        corte = getattr(post, "auto_cut_corte", None)
        analysis = getattr(corte, "analysis", None) if corte else None
        if not analysis:
            return (post.description or "")[:5000]

        video_name = (
            (analysis.name or "").strip()
            or ((analysis.source.title or "").strip() if getattr(analysis, "source", None) else "")
        )
        if not video_name and getattr(analysis, "file", None) and analysis.file.name:
            video_name = Path(analysis.file.name).stem
        if not video_name:
            video_name = "Vídeo original"

        convidados = (analysis.convidados or "").strip() or "-"
        lines = [
            f"🎙️ Corte da live: {video_name}",
            "",
            f"Convidado: {convidados}",
        ]
        youtube_url = (analysis.youtube_url or "").strip()
        if youtube_url:
            lines.extend(["", "📺 Episódio completo:", youtube_url])

        auto_part = "\n".join(lines).strip()
        brand_extra = ""
        if getattr(analysis, "brand", None):
            brand_extra = (analysis.brand.youtube_description_extra or "").strip()

        if brand_extra:
            # Duas linhas de separação entre parte automática e parte editável da marca.
            return f"{auto_part}\n\n\n{brand_extra}"[:5000]
        return auto_part[:5000]

    def _set_thumbnail_if_present(self, youtube, video_id: str | None, post: ScheduledPost | None) -> None:
        if not video_id or not post or not getattr(post, "auto_cut_corte_id", None):
            return
        corte = getattr(post, "auto_cut_corte", None)
        if not corte or not getattr(corte, "thumbnail", None):
            return
        thumb_path = corte.thumbnail.path
        media = MediaFileUpload(thumb_path, mimetype=None, resumable=False)
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        except HttpError as e:
            err = self._http_error_to_publish_error(e)
            # Não falha o upload do vídeo por erro de thumbnail, mas devolve rastreabilidade.
            raise YouTubePublishError(
                f"Thumbnail falhou: {err}",
                status_code=err.status_code,
                reason=err.reason,
                retriable=err.retriable,
            ) from e
        except Exception as e:
            raise YouTubePublishError(f"Thumbnail falhou: {e}", retriable=False) from e

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
        return scheduled_at.astimezone(dt_timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

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
        detail_msg = f"HTTP {status_code or '-'}"
        if reason:
            detail_msg += f" reason={reason}"
        if message:
            detail_msg += f" msg={message}"
        return YouTubePublishError(detail_msg, status_code=status_code, reason=reason, retriable=retriable)

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
            except (OSError, ConnectionError) as e:
                retry += 1
                if retry > MAX_RETRIES:
                    raise
                time.sleep(random.random() * (2**retry))
        raise RuntimeError("Upload falhou sem resposta")
