"""Publisher para Upload-Post.com (TikTok, X, Instagram)."""
import logging
import os
from datetime import timedelta, timezone as dt_timezone
from pathlib import Path

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

UPLOAD_POST_API_URL = "https://api.upload-post.com/api/upload"
PLATFORM_MAP = {
    "TIKTOK": "tiktok",
    "X": "x",  # Upload-Post usa "x", não "twitter"
    "INSTAGRAM": "instagram",
    "YOUTUBE": "youtube",
}


def _format_scheduled_date(scheduled_at, tz_name: str) -> str | None:
    """Formata scheduled_at para ISO-8601 em UTC. Retorna None se no passado ou muito próximo."""
    if not scheduled_at:
        return None
    if timezone.is_naive(scheduled_at):
        scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())
    # Só agenda se for pelo menos 2 min no futuro
    if scheduled_at <= timezone.now() + timedelta(minutes=2):
        return None
    utc_dt = scheduled_at.astimezone(dt_timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class UploadPostPublishError(Exception):
    """Erro ao publicar via Upload-Post."""

    def __init__(self, message: str, status_code: int | None = None, retriable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retriable = retriable


def publish_to_upload_post(
    video_path: str | Path,
    brand_id: int,
    platforms: list[str],
    title: str,
    description_by_platform: dict[str, str],
    scheduled_at=None,
    timezone_name: str = "America/Sao_Paulo",
) -> dict:
    """
    Publica vídeo no Upload-Post para as plataformas indicadas.
    platforms: ["TIKTOK", "X", "INSTAGRAM"]
    description_by_platform: {"TIKTOK": "...", "X": "...", "INSTAGRAM": "..."}
    scheduled_at: datetime para agendar (mesmo horário do YouTube). Se futuro, envia scheduled_date.
    timezone_name: IANA timezone (ex: America/Sao_Paulo).
    Retorna {"success": bool, "request_id": str?, "error": str?}
    """
    api_key = os.getenv("UPLOAD_POST_API_KEY") or getattr(settings, "UPLOAD_POST_API_KEY", "")
    if not api_key:
        raise UploadPostPublishError("UPLOAD_POST_API_KEY não configurada no .env", retriable=False)

    platform_codes = [PLATFORM_MAP.get(p.upper(), p.lower()) for p in platforms if p.upper() in PLATFORM_MAP]
    if not platform_codes:
        return {"success": False, "error": "Nenhuma plataforma válida"}

    path = Path(video_path)
    if not path.exists():
        raise UploadPostPublishError(f"Vídeo não encontrado: {path}", retriable=False)

    user = f"brand_{brand_id}"
    # Descrição: usa a primeira plataforma como base (Upload-Post pode aceitar uma só)
    description = description_by_platform.get(platforms[0], "") or title

    scheduled_date_str = _format_scheduled_date(scheduled_at, timezone_name)

    headers = {"Authorization": f"Apikey {api_key}"}
    try:
        with open(path, "rb") as f:
            files = {"video": (path.name, f, "video/mp4")}
            form_data = [
                ("user", user),
                ("title", (title or "Vídeo")[:200]),
                ("description", (description or "")[:2000]),
                ("timezone", timezone_name or "America/Sao_Paulo"),
            ]
            if scheduled_date_str:
                form_data.append(("scheduled_date", scheduled_date_str))
                logger.info("[UploadPost] Agendado para %s (timezone=%s)", scheduled_date_str, timezone_name)
            for pc in platform_codes:
                form_data.append(("platform[]", pc))
            # async_upload=true: retorna rápido com request_id, processa em background. Evita 504/499 em vídeos grandes.
            form_data.append(("async_upload", "true"))
            resp = requests.post(
                UPLOAD_POST_API_URL,
                headers=headers,
                files=files,
                data=form_data,
                timeout=300,
            )
    except Exception as e:
        logger.exception("[UploadPost] Erro ao enviar: %s", e)
        raise UploadPostPublishError(f"Erro de rede: {e}", retriable=True) from e

    if resp.status_code >= 400:
        err_msg = resp.text or f"HTTP {resp.status_code}"
        logger.warning("[UploadPost] Falha %s: %s", resp.status_code, err_msg[:500])
        raise UploadPostPublishError(
            err_msg[:500],
            status_code=resp.status_code,
            retriable=resp.status_code in (500, 502, 503, 504),
        )

    try:
        result = resp.json()
    except Exception:
        result = {}
    return {
        "success": True,
        "request_id": result.get("request_id"),
        "data": result,
    }
