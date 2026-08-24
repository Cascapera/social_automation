"""Conversão de segmentos Whisper para transcrição com timestamps.

`chunk_transcript` morava aqui e saiu quando a transcrição passou a ir ao LLM em bloco
único: ela era o resto do desenho de uma requisição por bloco, e sem chamador virava
convite a reintroduzir o overlap que custava 15% de input por análise.
"""

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
