"""Geração e queima de legendas com Whisper e FFmpeg."""

import logging
import os
import re
import traceback
from pathlib import Path

from django.conf import settings

from apps.jobs.services.ffmpeg import ffprobe_video_info, run_cmd, video_encode_args_burn_cpu

logger = logging.getLogger(__name__)

# Fonte com suporte a emojis (Windows). Linux: Noto Color Emoji. macOS: Apple Color Emoji
DEFAULT_FONT_EMOJI = "Segoe UI Emoji"


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
            for et, ow in zip(edited_tokens, original_words, strict=True)
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


def _transcribe_with_model(model, path: str, lang: str) -> list[dict]:
    """Transcreve arquivo com modelo Whisper já carregado."""
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


def load_whisper_model(
    model_size: str | None = None,
    device: str | None = None,
):
    """
    Carrega modelo Whisper uma vez. Reutilize para múltiplos arquivos (evita travamento na GPU).
    Retorna (model, model_size). model_size: None = WHISPER_MODEL do .env.
    """
    if model_size is None:
        model_size = os.getenv("WHISPER_MODEL", "large-v3").strip() or "large-v3"
    logger.info("Whisper: importando faster_whisper...")
    from faster_whisper import WhisperModel

    env_device = os.getenv("WHISPER_DEVICE", "").strip().lower()
    force_cpu = device == "cpu" or env_device == "cpu"
    debug_gpu = os.getenv("WHISPER_DEBUG_GPU", "").strip() in ("1", "true", "yes")
    target_device = "cpu" if force_cpu else "cuda"

    def _load(dev: str, compute: str):
        return WhisperModel(model_size, device=dev, compute_type=compute)

    logger.info(
        "Whisper: device=%s (param=%s, env=%r), modelo=%s",
        target_device, device, env_device or "(auto)", model_size,
    )
    try:
        if force_cpu:
            logger.info("Whisper: carregando modelo %s em CPU...", model_size)
            return _load("cpu", "int8"), model_size
        logger.info("Whisper: carregando modelo %s em CUDA (float16)...", model_size)
        return _load("cuda", "float16"), model_size
    except (RuntimeError, OSError) as e:
        err_str = str(e).lower()
        is_cuda_error = any(
            x in err_str for x in
            ("cublas", "cuda", "dll", "cudnn", "out of memory", "cuda error")
        )
        logger.exception("Whisper: ERRO ao carregar em %s - %s: %s", target_device, type(e).__name__, e)
        logger.info("Whisper: traceback:\n%s", traceback.format_exc())
        if debug_gpu:
            raise
        if is_cuda_error and not force_cpu:
            logger.warning("Whisper: fallback para CPU (int8)")
            return _load("cpu", "int8"), model_size
        raise


def generate_subtitles(
    video_path: Path,
    language: str = "pt",
    *,
    model_size: str | None = None,
    device: str | None = None,
    model=None,
) -> list[dict]:
    """
    Transcreve o vídeo com faster-whisper e retorna segmentos.
    Retorna: [{ "start": float, "end": float, "text": str, "words"?: [{ "start", "end", "word" }] }, ...]

    model: se fornecido, reutiliza (evita recarregar entre chunks na GPU).
    model_size/device: ignorados se model for fornecido.
    """
    if model is not None:
        logger.info("Whisper: reutilizando modelo. Transcrevendo %s...", video_path)
        result = _transcribe_with_model(model, str(video_path), language)
        logger.info("Whisper: transcrição concluída (%d segmentos)", len(result))
        return result

    if model_size is None:
        model_size = os.getenv("WHISPER_MODEL", "large-v3").strip() or "large-v3"
    logger.info("Whisper: importando faster_whisper...")
    from faster_whisper import WhisperModel

    env_device = os.getenv("WHISPER_DEVICE", "").strip().lower()
    force_cpu = device == "cpu" or env_device == "cpu"
    debug_gpu = os.getenv("WHISPER_DEBUG_GPU", "").strip() in ("1", "true", "yes")
    target_device = "cpu" if force_cpu else "cuda"

    def _load(dev: str, compute: str):
        return WhisperModel(model_size, device=dev, compute_type=compute)

    logger.info(
        "Whisper: device=%s (param=%s, env=%r), modelo=%s",
        target_device, device, env_device or "(auto)", model_size,
    )
    try:
        if force_cpu:
            logger.info("Whisper: carregando modelo %s em CPU...", model_size)
            model = _load("cpu", "int8")
        else:
            logger.info("Whisper: carregando modelo %s em CUDA (float16)...", model_size)
            model = _load("cuda", "float16")
        logger.info("Whisper: modelo carregado. Iniciando transcrição de %s...", video_path)
        result = _transcribe_with_model(model, str(video_path), language)
        logger.info("Whisper: transcrição concluída (%d segmentos)", len(result))
        return result
    except (RuntimeError, OSError) as e:
        err_str = str(e).lower()
        is_cuda_error = any(
            x in err_str for x in
            ("cublas", "cuda", "dll", "cudnn", "out of memory", "cuda error")
        )
        logger.exception(
            "Whisper: ERRO ao usar %s - %s: %s",
            target_device, type(e).__name__, e,
        )
        logger.info("Whisper: traceback completo:\n%s", traceback.format_exc())
        if debug_gpu:
            raise
        if is_cuda_error and not force_cpu:
            logger.warning("Whisper: fallback para CPU (int8)")
            model = _load("cpu", "int8")
            result = _transcribe_with_model(model, str(video_path), language)
            logger.info("Whisper: transcrição em CPU concluída (%d segmentos)", len(result))
            return result
        raise


def _sec_to_ass_tc(sec: float) -> str:
    """Segundos para formato ASS: H:MM:SS.cc"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int((sec % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def segments_to_ass_static_for_burn(
    segments: list[dict],
    playres_w: int,
    playres_h: int,
    style: dict,
) -> str:
    """
    ASS estático para queima em vídeo horizontal (16:9).
    PlayRes + Alignment=2 (base inferior) evitam legenda ao centro quando SRT+force_style
    é mal interpretado pelo libass no filtro subtitles do FFmpeg.
    """
    font = style.get("font", DEFAULT_FONT_EMOJI)
    size = max(8, min(72, int(style.get("size", 24))))
    color = _hex_to_ass_color(style.get("color", "#FFFFFF"))
    outline_color = _hex_to_ass_color(style.get("outline_color", "#000000"))
    outline = max(0, min(8, int(style.get("outline", 2))))
    margin_v = max(0, min(2000, int(style.get("margin_v", 160))))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {playres_w}\n"
        f"PlayResY: {playres_h}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, Shadow, Alignment, MarginV\n"
        f"Style: Default,{font},{size},{color},{outline_color},1,{outline},1,2,{margin_v}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for seg in segments:
        text = (seg.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        start_tc = _sec_to_ass_tc(float(seg.get("start", 0)))
        end_tc = _sec_to_ass_tc(float(seg.get("end", 0)))
        safe_text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        # {\an2} = base central (reforço; Style já Alignment=2)
        lines.append(f"Dialogue: 0,{start_tc},{end_tc},Default,,0,0,0,,{{\\an2}}{safe_text}")
    return header + "\n".join(lines)


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
        f"Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, Shadow, Alignment, MarginV\n"
        f"Style: Default,{DEFAULT_FONT_EMOJI},24,&H00FFFFFF,&H00000000,1,2,1,2,20\n\n"
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
    font = style.get("font", DEFAULT_FONT_EMOJI)
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


# Resolução de referência do ASS gerado a partir de SRT (libass usa 384x288 internamente).
# Shorts verticais: escalar MarginV para essa base evita legenda “no meio” em 9:16.
ASS_DEFAULT_PLAYRES_Y = 288


def _srt_tc_to_seconds(tc: str) -> float:
    tc = (tc or "").strip().replace(",", ".")
    if not tc:
        return 0.0
    parts = tc.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, TypeError):
        return 0.0


def _parse_srt_to_segments(raw: str) -> list[dict]:
    """Parse mínimo de SRT → [{start, end, text}, ...]."""
    segments: list[dict] = []
    raw = (raw or "").strip()
    if not raw:
        return segments
    for block in re.split(r"\r?\n\r?\n", raw):
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        i = 0
        if lines[0].isdigit():
            i = 1
        if i >= len(lines):
            continue
        time_line = lines[i]
        if "-->" not in time_line:
            continue
        left, right = time_line.split("-->", 1)
        start = _srt_tc_to_seconds(left.strip())
        end = _srt_tc_to_seconds(right.strip())
        text = " ".join(lines[i + 1 :]).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return segments


def burn_subtitles(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    style: dict | None = None,
    *,
    segments: list[dict] | None = None,
) -> None:
    """Queima legendas no vídeo usando FFmpeg."""
    style = dict(style or {})
    video_w = 1920
    video_h = 1080
    try:
        info = ffprobe_video_info(video_path)
        video_w = int(info.get("width") or 1920)
        video_h = int(info.get("height") or 1080)
        margin_desired = int(style.get("margin_v", 140))
        if video_w > video_h:
            # Longos horizontais (16:9): margem em px do vídeo; não escalar para 288 —
            # com original_size, libass alinha ao rodapé; escalar aqui empurrava a legenda ao centro.
            style["margin_v"] = max(24, margin_desired)
        else:
            # Verticais (shorts): manter escala para PlayRes ~288
            style["margin_v"] = max(10, int(margin_desired * ASS_DEFAULT_PLAYRES_Y / video_h))
    except Exception:
        pass

    is_horizontal = video_w > video_h
    # Longos 16:9: ASS com PlayRes + Alignment=2 — SRT+force_style no libass costuma ignorar alinhamento
    # e colocar a legenda ao centro (meio do ecrã).
    if is_horizontal:
        # Legendas animadas já vêm em .ass — não substituir por estático (preserva words/efeitos).
        if srt_path.suffix.lower() == ".ass":
            ass_str = str(srt_path.resolve()).replace("\\", "/")
            if ":" in ass_str:
                ass_str = ass_str.replace(":", "\\:")
            vf = f"subtitles='{ass_str}'"
        else:
            segs = segments
            if not segs:
                try:
                    segs = _parse_srt_to_segments(srt_path.read_text(encoding="utf-8"))
                except Exception:
                    segs = []
            if not segs:
                raise RuntimeError("burn_subtitles: sem segmentos para vídeo horizontal")
            ass_text = segments_to_ass_static_for_burn(segs, video_w, video_h, style)
            ass_path = srt_path.with_suffix(".ass")
            ass_path.write_text(ass_text, encoding="utf-8")
            ass_str = str(ass_path.resolve()).replace("\\", "/")
            if ":" in ass_str:
                ass_str = ass_str.replace(":", "\\:")
            vf = f"subtitles='{ass_str}'"
    else:
        force_style = build_ffmpeg_force_style(style)
        srt_str = str(srt_path.resolve()).replace("\\", "/")
        if ":" in srt_str:
            srt_str = srt_str.replace(":", "\\:")
        vf = f"subtitles='{srt_str}':force_style='{force_style}':original_size={video_w}x{video_h}"
    cmd = [
        settings.FFMPEG_BIN, "-y",
        "-i", str(video_path),
        "-vf", vf,
        *video_encode_args_burn_cpu(),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"burn subtitles failed: {res.stderr}")
