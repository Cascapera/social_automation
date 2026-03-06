"""Conversão de segmentos Whisper para transcrição com timestamps e chunking."""

from __future__ import annotations


def _sec_to_tc(sec: float) -> str:
    """Segundos para MM:SS ou HH:MM:SS."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def segments_to_transcript_with_timestamps(segments: list[dict]) -> str:
    """
    Converte segmentos Whisper [{start, end, text}] em texto com timestamps.
    Formato: [MM:SS] ou [HH:MM:SS] texto
    """
    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        tc = _sec_to_tc(start)
        lines.append(f"[{tc}] {text}")
    return "\n".join(lines)


def chunk_transcript(
    segments: list[dict],
    chunk_minutes: int = 18,
    overlap_minutes: int = 3,
    min_chunk_minutes: int = 5,
) -> list[dict]:
    """
    Divide segmentos em chunks com overlap.
    Retorna: [{start_sec, end_sec, segments: [...], text: "..."}]
    """
    if not segments:
        return []

    total_duration = segments[-1].get("end", 0)
    chunk_sec = chunk_minutes * 60
    overlap_sec = overlap_minutes * 60
    min_chunk_sec = min_chunk_minutes * 60

    chunks = []
    start_sec = 0.0

    while start_sec < total_duration:
        end_sec = min(start_sec + chunk_sec, total_duration)

        # Último chunk muito pequeno
        if chunks and (total_duration - start_sec) < min_chunk_sec:
            break

        chunks.append({"start_sec": start_sec, "end_sec": end_sec})
        start_sec = end_sec - overlap_sec
        if start_sec >= total_duration:
            break

    result = []
    for c in chunks:
        segs = [
            s
            for s in segments
            if s.get("end", 0) > c["start_sec"] and s.get("start", 0) < c["end_sec"]
        ]
        if not segs:
            continue
        text = segments_to_transcript_with_timestamps(segs)
        result.append(
            {
                "start_sec": c["start_sec"],
                "end_sec": c["end_sec"],
                "segments": segs,
                "text": text,
            }
        )

    return result
