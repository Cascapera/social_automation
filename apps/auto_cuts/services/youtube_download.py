"""Download de vídeos do YouTube via yt-dlp."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def download_youtube(url: str, output_path: Path) -> Path:
    """
    Baixa vídeo do YouTube (ou suportados pelo yt-dlp) para o caminho indicado.
    Retorna o Path do arquivo baixado.
    """
    import yt_dlp

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Template: base.%(ext)s para forçar nome e obter .mp4
    out_template = str(output_path.with_suffix("")) + ".%(ext)s"

    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise ValueError("Não foi possível obter informações do vídeo")
        filename = ydl.prepare_filename(info)
        result = Path(filename)
        if not result.exists():
            raise FileNotFoundError(f"Arquivo não foi gerado: {result}")
        return result
