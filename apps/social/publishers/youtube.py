"""Publisher para YouTube (videos longos e Shorts)."""
import random
import time
import json
from datetime import timedelta, timezone as dt_timezone
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace

from django.utils import timezone
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from apps.brands.models import BrandSocialAccount, BrandYouTubeCredential
from apps.jobs.models import ScheduledPost
from apps.social.publishers.base import BasePublisher
from apps.social.services.youtube_credentials import get_credentials

RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
MAX_RETRIES = 10


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
        interval_account = account
        if youtube_credential is not None and not getattr(account, "channel_id", ""):
            interval_account = SimpleNamespace(
                brand=getattr(account, "brand", None),
                channel_id=getattr(youtube_credential, "channel_id", ""),
                platform=getattr(account, "platform", "YTB"),
            )
        self._enforce_channel_min_interval(youtube, interval_account, post)
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
        if youtube_credential is not None:
            result["youtube_credential_id"] = youtube_credential.id
        if thumb_warning:
            result["warning"] = thumb_warning
        return result

    def _enforce_channel_min_interval(
        self,
        youtube,
        account: BrandSocialAccount,
        post: ScheduledPost | None,
    ) -> None:
        """
        Evita publicar se o canal ja teve postagem recente abaixo do intervalo minimo da brand.
        Usa publishedAt do ultimo video retornado pela API do YouTube.
        """
        if not post:
            return
        channel_id = (account.channel_id or "").strip()
        if not channel_id:
            return

        brand = getattr(account, "brand", None)
        if not brand:
            return

        platform_codes = {str(code).strip().upper() for code in (post.platforms or []) if code}
        if platform_codes == {"YT"}:
            min_interval_minutes = int(getattr(brand, "min_short_interval_minutes", 0) or 0)
        elif platform_codes == {"YTB"}:
            min_interval_minutes = int(getattr(brand, "min_long_interval_minutes", 0) or 0)
        else:
            platform = (account.platform or "").strip().upper()
            if platform == "YT":
                min_interval_minutes = int(getattr(brand, "min_short_interval_minutes", 0) or 0)
            else:
                min_interval_minutes = int(getattr(brand, "min_long_interval_minutes", 0) or 0)
        if min_interval_minutes <= 0:
            return

        try:
            resp = youtube.search().list(
                part="snippet",
                channelId=channel_id,
                type="video",
                order="date",
                maxResults=1,
            ).execute()
        except HttpError as e:
            err = self._http_error_to_publish_error(e)
            # Se falhar a leitura, segue sem bloquear publicação.
            if err.retriable:
                raise YouTubePublishError(
                    f"Falha ao consultar ultimo video do canal: {err}",
                    status_code=err.status_code,
                    reason=err.reason,
                    retriable=True,
                ) from e
            return
        except Exception:
            return

        items = (resp or {}).get("items") or []
        if not items:
            return
        published_at = (
            items[0].get("snippet", {}).get("publishedAt")
            if isinstance(items[0], dict)
            else None
        )
        if not published_at:
            return
        try:
            published_dt = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            ).astimezone(dt_timezone.utc)
        except Exception:
            return

        now_utc = timezone.now().astimezone(dt_timezone.utc)
        elapsed = (now_utc - published_dt).total_seconds()
        required = min_interval_minutes * 60
        if elapsed >= required:
            return

        wait_seconds = int(required - elapsed)
        wait_minutes = max(1, int((wait_seconds + 59) / 60))
        raise YouTubePublishError(
            f"Intervalo minimo entre publicacoes ainda nao cumprido ({min_interval_minutes} min). "
            f"Aguardar cerca de {wait_minutes} min.",
            reason="minIntervalNotReached",
            retriable=True,
            retry_after_seconds=max(wait_seconds, 60),
        )

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
        retry_after_seconds = None
        if reason == "quotaExceeded":
            retriable = True
            retry_after_seconds = self._seconds_until_youtube_quota_reset()
        detail_msg = f"HTTP {status_code or '-'}"
        if reason:
            detail_msg += f" reason={reason}"
        if message:
            detail_msg += f" msg={message}"
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
            except (OSError, ConnectionError) as e:
                retry += 1
                if retry > MAX_RETRIES:
                    raise
                time.sleep(random.random() * (2**retry))
        raise RuntimeError("Upload falhou sem resposta")
