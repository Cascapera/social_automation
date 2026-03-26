"""Extract video/audio chunks for chunked transcription (avoids crashes on long videos)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from django.conf import settings


def get_cortes_processo_dir(analysis_id: int) -> Path:
    """Folder for chunks in progress: cortes_processo/<analysis_id>/"""
    base = Path(settings.MEDIA_ROOT) / "cortes_processo"
    return base / str(analysis_id)


def extract_chunks_to_folder(
    video_path: Path,
    analysis_id: int,
    chunk_minutes: int = 18,
    overlap_minutes: int = 3,
) -> list[tuple[Path, float, float]]:
    """
    Extract audio chunks from video and save under cortes_processo/<analysis_id>/.
    Returns list of (chunk_path, start_sec, end_sec).
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
        chunk_path = folder / f"chunk_{i:03d}.wav"
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
    """Transcribe one chunk. Returns {start_sec, end_sec, text, segments}."""
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
    Transcribe each chunk on disk, one at a time. Load the model ONCE and reuse
    (avoids GPU reload hangs between chunks).
    Yields: {"start_sec", "end_sec", "text", "segments"} per chunk.
    model_size: None = WHISPER_MODEL from .env or "small" (for long videos).
    model: if provided, use instead of loading (avoids crash when exiting generator on long videos).
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
        # Do not unlink here — on long videos it can crash on Windows.
        # cleanup_cortes_processo removes the whole folder at the end.


def cleanup_cortes_processo(analysis_id: int) -> None:
    """Remove folder cortes_processo/<analysis_id>/ and all contents."""
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
    Return list of (start_sec, end_sec) per chunk with overlap.
    E.g. 18 min chunks, 3 min overlap → (0,1080), (900,1980), (1800,2880)...
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

        # Avoid a tiny last chunk
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
    Extract an audio segment from video for transcription (chunks).

    Writes 16 kHz mono PCM WAV (Whisper-friendly). We avoid AAC-in → AAC-out here:
    marginal/corrupt AAC from some YouTube remuxes can confuse the decoder when
    combined with ``-err_detect ignore_err`` / ``discardcorrupt``, sometimes producing
    bogus channel layouts (e.g. 33 ch) and breaking swresample. Decoding to PCM with
    an explicit mono ``pan`` keeps the graph stable. Opus/other codecs are decoded
    normally to PCM.
    """
    import subprocess

    # -ss before -i: fast input seek (long sources). No discardcorrupt/ignore_err here:
    # dropping AAC packets can desync the decoder; tolerating decode errors can yield
    # garbage channel counts mid-stream.
    cmd = [
        settings.FFMPEG_BIN,
        "-y",
        "-ss", str(start_sec),
        "-t", str(duration_sec),
        "-i", str(video_path),
        "-vn",
        "-map", "0:a:0",
        # First channel only: avoids rematrix failures when the native decoder briefly
        # reports impossible layouts on damaged AAC. Speech is usually centered; for
        # true stereo-only-right content this may be weaker than a full downmix.
        "-af", "pan=mono|c0=c0",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        "-f", "wav",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg extract failed: {result.stderr}")


def _segments_to_chunk_text(segments: list[dict]) -> str:
    """Format segments as [MM:SS] or [HH:MM:SS] text."""
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
