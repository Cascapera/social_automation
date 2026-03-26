"""Extração de cortes de vídeo para AutoCutCorte."""

from pathlib import Path

from apps.jobs.services.ffmpeg import cut_clip


def extract_corte(
    video_path: Path,
    start_tc: str,
    end_tc: str,
    output_path: Path,
    use_gpu: bool = False,
) -> Path:
    """Extrai trecho do vídeo com FFmpeg. Retorna path do arquivo criado."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cut_clip(video_path, start_tc, end_tc, output_path, use_gpu=use_gpu)
    return output_path
