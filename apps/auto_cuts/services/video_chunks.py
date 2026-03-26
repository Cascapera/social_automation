"""Extração de chunks de vídeo/áudio para transcrição em partes (evita crash em vídeos longos)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from django.conf import settings


def get_cortes_processo_dir(analysis_id: int) -> Path:
    """Pasta para chunks em processamento: cortes_processo/<analysis_id>/"""
    base = Path(settings.MEDIA_ROOT) / "cortes_processo"
    return base / str(analysis_id)


def extract_chunks_to_folder(
    video_path: Path,
    analysis_id: int,
    chunk_minutes: int = 18,
    overlap_minutes: int = 3,
) -> list[tuple[Path, float, float]]:
    """
    Extrai chunks de áudio do vídeo e salva em cortes_processo/<analysis_id>/.
    Retorna lista de (chunk_path, start_sec, end_sec).
    """
    from apps.jobs.services.ffmpeg import ffprobe_duration

    duration = ffprobe_duration(video_path)
    boundaries = get_chunk_boundaries(
        duration,
        chunk_minutes=chunk_minutes,
        overlap_minutes=overlap_minutes,
    )
    if not boundaries:
        return []

    folder = get_cortes_processo_dir(analysis_id)
    folder.mkdir(parents=True, exist_ok=True)

    result = []
    for i, (start_sec, end_sec) in enumerate(boundaries, 1):
        chunk_duration = end_sec - start_sec
        chunk_path = folder / f"chunk_{i:03d}.m4a"
        extract_audio_chunk(video_path, start_sec, chunk_duration, chunk_path)
        result.append((chunk_path, start_sec, end_sec))
    return result


def transcribe_single_chunk(
    model,
    chunk_path: Path,
    start_sec: float,
    end_sec: float,
    language: str = "pt",
) -> dict:
    """Transcreve um chunk. Retorna {start_sec, end_sec, text, segments}."""
    from apps.jobs.services.subtitles import generate_subtitles

    if not chunk_path.exists():
        return {"start_sec": start_sec, "end_sec": end_sec, "text": "", "segments": []}
    segments = generate_subtitles(chunk_path, language=language, model=model)
    for seg in segments:
        seg["start"] = seg.get("start", 0) + start_sec
        seg["end"] = seg.get("end", 0) + start_sec
        seg["text"] = seg.get("text", "").strip()
        if seg.get("words"):
            for w in seg["words"]:
                w["start"] = w.get("start", 0) + start_sec
                w["end"] = w.get("end", 0) + start_sec
    text = _segments_to_chunk_text(segments)
    return {"start_sec": start_sec, "end_sec": end_sec, "text": text, "segments": segments}


def transcribe_chunks_one_by_one(
    chunk_paths: list[tuple[Path, float, float]],
    language: str = "pt",
    *,
    model_size: str | None = None,
    model=None,
):
    """
    Transcreve cada chunk em disco, um por vez. Carrega o modelo UMA vez e reutiliza
    (evita travamento na GPU ao recarregar entre chunks).
    Yields: {"start_sec", "end_sec", "text", "segments"} para cada chunk.
    model_size: None = WHISPER_MODEL do .env ou "small" (para vídeos longos).
    model: se fornecido, usa em vez de carregar (evita crash ao sair do gerador em vídeos longos).
    """
    from apps.jobs.services.subtitles import generate_subtitles, load_whisper_model

    if model is None:
        if model_size is None:
            model_size = os.getenv("WHISPER_MODEL", "small").strip() or "small"
        model, _ = load_whisper_model(model_size=model_size, device=None)

    for chunk_path, start_sec, end_sec in chunk_paths:
        if not chunk_path.exists():
            continue
        segments = generate_subtitles(
            chunk_path, language=language, model=model
        )
        for seg in segments:
            seg["start"] = seg.get("start", 0) + start_sec
            seg["end"] = seg.get("end", 0) + start_sec
            seg["text"] = seg.get("text", "").strip()
            if seg.get("words"):
                for w in seg["words"]:
                    w["start"] = w.get("start", 0) + start_sec
                    w["end"] = w.get("end", 0) + start_sec

        text = _segments_to_chunk_text(segments)
        yield {"start_sec": start_sec, "end_sec": end_sec, "text": text, "segments": segments}
        # Não unlink aqui - em vídeos longos pode causar crash no Windows.
        # cleanup_cortes_processo remove a pasta inteira no final.


def cleanup_cortes_processo(analysis_id: int) -> None:
    """Remove pasta cortes_processo/<analysis_id>/ e todo conteúdo."""
    folder = get_cortes_processo_dir(analysis_id)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)


def get_chunk_boundaries(
    duration_sec: float,
    chunk_minutes: int = 18,
    overlap_minutes: int = 3,
    min_chunk_minutes: int = 5,
) -> list[tuple[float, float]]:
    """
    Retorna lista de (start_sec, end_sec) para cada chunk com overlap.
    Ex: 18min chunks, 3min overlap → (0,1080), (900,1980), (1800,2880)...
    """
    if duration_sec <= 0:
        return []

    chunk_sec = chunk_minutes * 60
    overlap_sec = overlap_minutes * 60
    min_chunk_sec = min_chunk_minutes * 60

    boundaries = []
    start_sec = 0.0

    while start_sec < duration_sec:
        end_sec = min(start_sec + chunk_sec, duration_sec)

        # Evita último chunk muito pequeno
        if boundaries and (duration_sec - start_sec) < min_chunk_sec:
            break

        boundaries.append((start_sec, end_sec))
        start_sec = end_sec - overlap_sec
        if start_sec >= duration_sec:
            break

    return boundaries


def extract_audio_chunk(
    video_path: Path,
    start_sec: float,
    duration_sec: float,
    output_path: Path,
) -> None:
    """
    Extrai trecho de áudio do vídeo para transcrição (chunks).

    Não usa -c:a copy: vídeos do YouTube com faixa EN podem vir em Opus, que o container .m4a
    (muxer ipod) não aceita em copy — gera "Could not find tag for codec opus".
    Reencode para AAC (leve), compatível com Whisper e M4A.
    """
    import subprocess

    # -ss antes de -i para seek rápido (input seeking)
    cmd = [
        settings.FFMPEG_BIN,
        "-y",
        "-ss", str(start_sec),
        "-t", str(duration_sec),
        "-i", str(video_path),
        "-vn",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg extract failed: {result.stderr}")


def _segments_to_chunk_text(segments: list[dict]) -> str:
    """Formata segmentos como [MM:SS] ou [HH:MM:SS] texto."""
    def _sec_to_tc(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        tc = _sec_to_tc(start)
        lines.append(f"[{tc}] {text}")
    return "\n".join(lines)
