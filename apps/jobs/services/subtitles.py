"""Geração e queima de legendas com Whisper e FFmpeg."""

from pathlib import Path
import tempfile

from django.conf import settings

from apps.jobs.services.ffmpeg import run_cmd


def align_edited_to_original_words(edited_text: str, original_words: list[dict]) -> list[dict] | None:
    """
    Alinha texto editado aos timestamps originais das palavras.
    Preserva timestamps quando o usuário só altera pontuação ou pequenos detalhes.
    Para mudanças maiores (mais/menos palavras), distribui proporcionalmente.
    """
    if not original_words:
        return None
    edited_tokens = [t for t in edited_text.split() if t]
    if not edited_tokens:
        return None

    seg_start = original_words[0]["start"]
    seg_end = original_words[-1]["end"]
    total_dur = seg_end - seg_start

    if len(edited_tokens) == len(original_words):
        # 1:1 – usa timestamps originais para cada token editado
        return [
            {"start": ow["start"], "end": ow["end"], "word": et}
            for et, ow in zip(edited_tokens, original_words)
        ]

    if len(edited_tokens) > len(original_words):
        # Mais tokens – distribui o tempo proporcionalmente pelo tamanho
        total_chars = sum(len(t) for t in edited_tokens) or 1
        result = []
        t = seg_start
        for i, tok in enumerate(edited_tokens):
            if i == len(edited_tokens) - 1:
                end = seg_end
            else:
                frac = len(tok) / total_chars
                end = t + total_dur * frac
            result.append({"start": t, "end": end, "word": tok})
            t = end
        return result

    # Menos tokens – agrupa timestamps
    n_orig, n_edit = len(original_words), len(edited_tokens)
    result = []
    for i, tok in enumerate(edited_tokens):
        j0 = int(i * n_orig / n_edit)
        j1 = min(int((i + 1) * n_orig / n_edit), n_orig)
        if j1 <= j0:
            j1 = j0 + 1
        ow_start = original_words[j0]["start"]
        ow_end = original_words[j1 - 1]["end"]
        result.append({"start": ow_start, "end": ow_end, "word": tok})
    return result


def generate_subtitles(video_path: Path, language: str = "pt") -> list[dict]:
    """
    Transcreve o vídeo com faster-whisper e retorna segmentos.
    Retorna: [{ "start": float, "end": float, "text": str, "words"?: [{ "start", "end", "word" }] }, ...]
    Usa word_timestamps para permitir legendas animadas (palavra por palavra).
    """
    from faster_whisper import WhisperModel

    def _transcribe(model, path: str, lang: str) -> list[dict]:
        segments, _ = model.transcribe(
            path, language=lang, word_timestamps=True, without_timestamps=False
        )
        result = []
        for s in segments:
            seg = {"start": s.start, "end": s.end, "text": s.text.strip()}
            if s.words:
                seg["words"] = [
                    {"start": w.start, "end": w.end, "word": w.word}
                    for w in s.words
                ]
            result.append(seg)
        return result

    try:
        model = WhisperModel("large-v3", device="cuda", compute_type="float16")
        return _transcribe(model, str(video_path), language)
    except RuntimeError as e:
        err = str(e).lower()
        if any(x in err for x in ("cublas", "cuda", "dll", "cudnn")):
            model = WhisperModel("large-v3", device="cpu", compute_type="int8")
            return _transcribe(model, str(video_path), language)
        raise


def _sec_to_ass_tc(sec: float) -> str:
    """Segundos para formato ASS: H:MM:SS.cc"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int((sec % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def segments_to_srt(segments: list[dict]) -> str:
    """Converte segmentos para formato SRT."""
    def sec_to_tc(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, 1):
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = seg.get("text", "").replace("\n", " ")
        if not text:
            continue
        lines.append(f"{i}\n{sec_to_tc(start)} --> {sec_to_tc(end)}\n{text}\n")
    return "\n".join(lines)


def segments_to_ass_animated(segments: list[dict]) -> str:
    """
    Converte segmentos com words para ASS com efeito de palavras acumulando.
    Cada linha mostra o texto acumulado até aquela palavra, aparecendo no timestamp da palavra.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, Shadow, Alignment, MarginV\n"
        "Style: Default,Arial,24,&H00FFFFFF,&H00000000,1,2,1,2,20\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for seg in segments:
        words = seg.get("words") or []
        text = (seg.get("text") or "").replace("\n", " ")
        if not text:
            continue
        if words:
            accumulated = ""
            for i, w in enumerate(words):
                word = (w.get("word") or "").strip()
                if not word:
                    continue
                accumulated = (accumulated + " " + word).strip() if accumulated else word
                start = w.get("start", seg["start"])
                end = w.get("end", seg["end"])
                if i + 1 < len(words) and words[i + 1].get("start") is not None:
                    end = words[i + 1]["start"]
                else:
                    end = seg.get("end", end)
                start_tc = _sec_to_ass_tc(start)
                end_tc = _sec_to_ass_tc(end)
                safe_text = accumulated.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                lines.append(f"Dialogue: 0,{start_tc},{end_tc},Default,,0,0,0,,{safe_text}")
        else:
            start_tc = _sec_to_ass_tc(seg.get("start", 0))
            end_tc = _sec_to_ass_tc(seg.get("end", 0))
            safe_text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            lines.append(f"Dialogue: 0,{start_tc},{end_tc},Default,,0,0,0,,{safe_text}")
    return header + "\n".join(lines)


def _hex_to_ass_color(hex_color: str) -> str:
    """Converte #RRGGBB para &HAABBGGRR (ASS format)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"&H00{b:02X}{g:02X}{r:02X}"
    return "&H00FFFFFF"


def build_ffmpeg_force_style(style: dict | None) -> str:
    """Constrói string force_style para FFmpeg."""
    if not style:
        style = {}
    font = style.get("font", "Arial")
    size = style.get("size", 24)
    color = _hex_to_ass_color(style.get("color", "#FFFFFF"))
    outline_color = _hex_to_ass_color(style.get("outline_color", "#000000"))
    outline = style.get("outline", 2)
    position = style.get("position", "bottom")
    alignment = {"bottom": 2, "center": 5, "top": 8}.get(position, 2)
    margin_v = style.get("margin_v", 20)

    return (
        f"FontName={font},FontSize={size},"
        f"PrimaryColour={color},OutlineColour={outline_color},"
        f"BorderStyle=1,Outline={outline},Shadow=1,"
        f"Alignment={alignment},MarginV={margin_v}"
    )


def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path, style: dict | None = None) -> None:
    """Queima legendas no vídeo usando FFmpeg."""
    force_style = build_ffmpeg_force_style(style)
    # Path para FFmpeg: no Windows, usar / e escapar :
    srt_str = str(srt_path.resolve()).replace("\\", "/")
    if ":" in srt_str:
        srt_str = srt_str.replace(":", "\\:")
    vf = f"subtitles='{srt_str}':force_style='{force_style}'"
    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"burn subtitles failed: {res.stderr}")
