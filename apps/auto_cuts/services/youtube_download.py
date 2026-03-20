"""Download de videos do YouTube via yt-dlp."""

import logging
from pathlib import Path
import time

logger = logging.getLogger(__name__)


def download_youtube(
    url: str,
    output_path: Path,
    *,
    preferred_audio_language: str | None = None,
) -> Path:
    """
    Baixa vídeo do YouTube (ou suportados pelo yt-dlp) para o caminho indicado.
    Retorna o Path do arquivo baixado.

    preferred_audio_language:
        Quando "en", prioriza faixa de áudio em inglês (evita dublagem automática em outro idioma
        quando o vídeo oferece várias faixas). Modos PT não precisam passar (canais BR).
    """
    import yt_dlp

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Template: base.%(ext)s para forcar nome e obter .mp4
    out_template = str(output_path.with_suffix("")) + ".%(ext)s"

    # Fallbacks para reduzir falhas de rede/CDN:
    # Com EN: tenta primeiro áudio com idioma en (en, en-US, etc.) antes do melhor áudio genérico.
    if (preferred_audio_language or "").strip().lower() == "en":
        format_candidates = [
            "bestvideo[ext=mp4]+bestaudio[language^=en]/bestvideo[ext=mp4]+bestaudio[ext=m4a]",
            "bestvideo[ext=mp4]+bestaudio[language=en]/bestvideo[ext=mp4]+bestaudio[ext=m4a]",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
            "best[ext=mp4]",
            "best",
        ]
        logger.info("[YTDLP] Preferindo faixa de áudio em inglês (modo análise EN)")
    else:
        format_candidates = [
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
            "best[ext=mp4]",
            "best",
        ]
    last_exc: Exception | None = None

    for idx, fmt in enumerate(format_candidates, start=1):
        opts = {
            "format": fmt,
            "outtmpl": out_template,
            "merge_output_format": "mp4",
            "quiet": False,
            "no_warnings": False,
            "retries": 6,
            "fragment_retries": 6,
            "extractor_retries": 3,
            "file_access_retries": 3,
            "socket_timeout": 25,
            # Evita rota IPv6 instavel em alguns hosts/CDNs no Docker/WSL.
            "force_ipv4": True,
        }
        try:
            logger.info("[YTDLP] Tentativa %s com formato: %s", idx, fmt)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise ValueError("Nao foi possivel obter informacoes do video")
                filename = ydl.prepare_filename(info)
                result = Path(filename)
                if not result.exists():
                    raise FileNotFoundError(f"Arquivo nao foi gerado: {result}")
                return result
        except Exception as exc:
            last_exc = exc
            logger.warning("[YTDLP] Falha na tentativa %s (%s): %s", idx, fmt, exc)
            # pequeno backoff entre tentativas para reduzir erro de rede temporario
            time.sleep(min(4 * idx, 12))

    raise RuntimeError(f"Falha ao baixar video do YouTube apos fallbacks: {last_exc}")
