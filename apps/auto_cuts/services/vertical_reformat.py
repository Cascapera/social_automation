"""
Reenquadramento vertical para cortes automáticos (16:9 → 9:16).

Duas opções:
- frame_center: Enquadrar e centralizar (vídeo centralizado, bordas coloridas, logo/título/texto)
- zoom_crop: Zoom e corte (preenche 80% da altura, 10% margem)

Título e texto adicional: renderizados com Pillow para emojis coloridos (FFmpeg drawtext só monocromático).
"""

import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from apps.jobs.services.ffmpeg import (
    audio_encode_args,
    common_mp4_flags,
    ffprobe_duration,
    ffprobe_video_info,
    input_has_audio,
    run_cmd,
    video_encode_args,
)

logger = logging.getLogger(__name__)

# Frame vertical padrão (Shorts/Reels/TikTok)
OUTPUT_W = 1080
OUTPUT_H = 1920


def _is_emoji_char(c: str) -> bool:
    """Verifica se o caractere é emoji (Unicode)."""
    if not c:
        return False
    code = ord(c)
    if 0x2600 <= code <= 0x26FF or 0x2700 <= code <= 0x27BF:
        return True
    if 0x1F300 <= code <= 0x1F9FF or 0x1F600 <= code <= 0x1F64F:
        return True
    if 0x1F680 <= code <= 0x1F6FF or 0x1F900 <= code <= 0x1F9FF:
        return True
    if 0x1FA00 <= code <= 0x1FA6F or 0x1F1E0 <= code <= 0x1F1FF:
        return True
    if code == 0xFE0F:  # Variation selector (parte de sequência emoji)
        return True
    return False


def _get_text_emoji_runs(s: str) -> list[tuple[str, bool]]:
    """Divide texto em runs (texto, emoji). Retorna [(run, is_emoji), ...]."""
    if not s:
        return []
    runs = []
    current = []
    current_is_emoji = None
    for c in s:
        is_emoji = _is_emoji_char(c)
        if current_is_emoji is not None and current_is_emoji != is_emoji:
            if current:
                runs.append(("".join(current), current_is_emoji))
            current = []
        current_is_emoji = is_emoji
        current.append(c)
    if current:
        runs.append(("".join(current), current_is_emoji))
    return runs


def _hex_to_pil_color(hex_color: str) -> tuple[int, int, int]:
    """Converte #RRGGBB para (r, g, b)."""
    h = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(h) >= 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return (255, 255, 255)


def _get_emoji_font_path() -> Path | None:
    """Caminho da fonte de emoji colorido. Windows: Segoe UI Emoji."""
    if os.name == "nt":
        p = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "Fonts" / "seguiemj.ttf"
        if p.exists():
            return p
    # Linux: tentar Noto Color Emoji
    for path in [
        Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
        Path("/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf"),
    ]:
        if path.exists():
            return path
    return None


def _get_text_font_path() -> Path | None:
    """Caminho da fonte para texto regular."""
    if os.name == "nt":
        for name in ["segoeui.ttf", "arial.ttf"]:
            p = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "Fonts" / name
            if p.exists():
                return p
    return None


def _render_text_overlay_pillow(
    output_path: Path,
    title: str,
    custom_text: str,
    y_title: int,
    y_text: int,
    font_size_title: int = 36,
    font_size_text: int = 28,
    title_color: str = "#FFFFFF",
    text_color: str = "#FFFFFF",
) -> bool:
    """
    Renderiza título e texto em PNG com emojis coloridos (Pillow).
    Retorna True se gerou; False se fallback para drawtext.
    """
    emoji_font_path = _get_emoji_font_path()
    text_font_path = _get_text_font_path()
    if not emoji_font_path or not text_font_path:
        logger.warning("Fontes para emoji colorido não encontradas; usando drawtext.")
        return False

    try:
        emoji_font = ImageFont.truetype(str(emoji_font_path), font_size_title)
        emoji_font_small = ImageFont.truetype(str(emoji_font_path), font_size_text)
        text_font = ImageFont.truetype(str(text_font_path), font_size_title)
        text_font_small = ImageFont.truetype(str(text_font_path), font_size_text)
    except Exception as e:
        logger.warning("Erro ao carregar fontes: %s", e)
        return False

    img = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    tc = _hex_to_pil_color(title_color)
    txc = _hex_to_pil_color(text_color)

    def _draw_text_line(text: str, y: int, font_size: int, color: tuple, is_title: bool) -> None:
        if not text.strip():
            return
        runs = _get_text_emoji_runs(text.strip())
        if not runs:
            return
        total_w = 0
        for run, is_emoji in runs:
            f = (emoji_font if is_title else emoji_font_small) if is_emoji else (text_font if is_title else text_font_small)
            bbox = draw.textbbox((0, 0), run, font=f)
            total_w += bbox[2] - bbox[0]
        x = (OUTPUT_W - total_w) // 2
        for run, is_emoji in runs:
            f = (emoji_font if is_title else emoji_font_small) if is_emoji else (text_font if is_title else text_font_small)
            kw = {"font": f, "fill": color}
            if is_emoji:
                kw["embedded_color"] = True
            draw.text((x, y), run, **kw)
            bbox = draw.textbbox((x, y), run, font=f)
            x = bbox[2]

    if title and title.strip():
        _draw_text_line(title.strip(), y_title, font_size_title, tc, is_title=True)
    if custom_text and custom_text.strip():
        y_custom = y_text if (title and title.strip()) else y_title
        _draw_text_line(custom_text.strip(), y_custom, font_size_text, txc, is_title=False)

    img.save(output_path, "PNG")
    return True


def _hex_to_ffmpeg_color(hex_color: str) -> str:
    """Converte #RRGGBB para 0xRRGGBBAA (FFmpeg)."""
    hex_color = (hex_color or "#000000").strip().lstrip("#")
    if len(hex_color) == 6:
        return f"0x{hex_color}FF"
    if len(hex_color) == 8:
        return f"0x{hex_color}"
    return "0x000000FF"


def is_source_16_9(video_path: Path) -> bool:
    """Verifica se o vídeo tem proporção ~16:9."""
    info = ffprobe_video_info(video_path)
    w, h = info.get("width", 0), info.get("height", 0)
    if not w or not h:
        return False
    aspect = w / h
    return 1.7 <= aspect <= 1.8


def is_source_horizontal(video_path: Path) -> bool:
    """Verifica se o vídeo é horizontal (width > height). Usado para reenquadrar para vertical."""
    info = ffprobe_video_info(video_path)
    w, h = info.get("width", 0), info.get("height", 0)
    return w > 0 and h > 0 and w > h


def _hex_to_drawtext_color(hex_color: str) -> str:
    """Converte #RRGGBB para 0xRRGGBB (FFmpeg drawtext fontcolor)."""
    h = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(h) == 6:
        return f"0x{h}"
    return "0xFFFFFF"


def _escape_drawtext(t: str) -> str:
    r"""Escapa texto para FFmpeg drawtext: backslash, aspas e dois-pontos."""
    if not t:
        return ""
    # : separa opções no FFmpeg; em texto precisa escapar com \:
    return t.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")


# Fonte com emojis: fontfile no Windows (mais confiável que font=)
def _get_emoji_font_opt() -> str:
    """Retorna opção de fonte para drawtext (emojis). Windows: fontfile. Outros: font=."""
    if os.name == "nt":
        font_path = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Fonts", "seguiemj.ttf")
        if os.path.exists(font_path):
            # FFmpeg aceita / no path no Windows
            path_esc = font_path.replace("\\", "/").replace(":", "\\:")
            return f"fontfile='{path_esc}':"
    return "font='Segoe UI Emoji':"  # Linux/macOS podem usar fontconfig


def _build_drawtext_filters(
    title: str,
    custom_text: str,
    y_title: int,
    y_text: int,
    font_size_title: int = 36,
    font_size_text: int = 28,
    title_color: str = "#FFFFFF",
    text_color: str = "#FFFFFF",
) -> str:
    """
    Constrói filtros drawtext para título e texto.
    Usa fonte com emojis (Segoe UI Emoji no Windows).
    """
    tc = _hex_to_drawtext_color(title_color)
    txc = _hex_to_drawtext_color(text_color)
    font_opt = _get_emoji_font_opt()
    parts = []
    if title and title.strip():
        escaped = _escape_drawtext(title.strip())
        parts.append(f"drawtext=text='{escaped}':{font_opt}expansion=none:fontsize={font_size_title}:fontcolor={tc}:x=(w-text_w)/2:y={y_title}")
    if custom_text and custom_text.strip():
        escaped = _escape_drawtext(custom_text.strip())
        y = y_text if parts else y_title
        parts.append(f"drawtext=text='{escaped}':{font_opt}expansion=none:fontsize={font_size_text}:fontcolor={txc}:x=(w-text_w)/2:y={y}")
    return ",".join(parts) if parts else ""


def reformat_video_vertical(
    input_path: Path,
    output_path: Path,
    mode: str,
    *,
    background_color: str = "#000000",
    logo_path: Path | None = None,
    title: str = "",
    custom_text: str = "",
    font_size_title: int = 36,
    font_size_text: int = 28,
    title_color: str = "#FFFFFF",
    text_color: str = "#FFFFFF",
    use_gpu: bool = False,
) -> None:
    """
    Reenquadra vídeo horizontal para vertical 9:16.

    mode: "frame_center" ou "zoom_crop"
    """
    if mode not in ("frame_center", "zoom_crop"):
        raise ValueError(f"mode deve ser frame_center ou zoom_crop, recebido: {mode}")

    bg = _hex_to_ffmpeg_color(background_color)
    fps = "30"
    has_audio = input_has_audio(input_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        inputs = ["-i", str(input_path)]
        next_idx = 1

        if mode == "frame_center":
            # Corta 15% da lateral direita do vídeo original (remove painéis laterais)
            vf_base = (
                f"[0:v]crop=iw*85/100:ih:0:0,"
                f"scale={OUTPUT_W}:-2,"
                f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:color={bg},"
                f"fps={fps},format=yuv420p[v0]"
            )
        else:
            # zoom_crop: corta 15% direita, preenche 80% altura, crop central
            fill_h = int(OUTPUT_H * 0.8)
            fill_w = int(fill_h * 16 / 9)
            vf_base = (
                f"[0:v]crop=iw*85/100:ih:0:0,"
                f"scale={fill_w}:{fill_h}:force_original_aspect_ratio=increase,"
                f"crop={OUTPUT_W}:{fill_h}:(iw-{OUTPUT_W})/2:(ih-{fill_h})/2,"
                f"pad={OUTPUT_W}:{OUTPUT_H}:0:(oh-ih)/2:color={bg},"
                f"fps={fps},format=yuv420p[v0]"
            )

        filter_parts = [vf_base]
        current = "[v0]"

        if mode == "frame_center":
            if logo_path and logo_path.exists():
                inputs += ["-i", str(logo_path)]
                logo_idx = next_idx
                next_idx += 1
                # Logo canto sup esquerdo: 80x80 px, opacidade 80%, 40px margem topo e esquerda
                filter_parts.append(
                    f"[{logo_idx}:v]scale=80:80:force_original_aspect_ratio=decrease,format=rgba,colorchannelmixer=aa=0.8[logo];"
                    f"{current}[logo]overlay=40:40:format=auto[v1]"
                )
                current = "[v1]"

            if title or custom_text:
                y_title = 1400
                y_text = y_title + 144  # ~100px abaixo do título para evitar sobreposição
                # Tentar Pillow para emojis coloridos; fallback para drawtext
                overlay_png = tmppath / "title_overlay.png"
                use_pillow = _render_text_overlay_pillow(
                    overlay_png,
                    title or "",
                    custom_text or "",
                    y_title,
                    y_text,
                    font_size_title=font_size_title,
                    font_size_text=font_size_text,
                    title_color=title_color,
                    text_color=text_color,
                )
                if use_pillow and overlay_png.exists():
                    inputs += ["-i", str(overlay_png)]
                    overlay_idx = next_idx
                    next_idx += 1
                    filter_parts.append(
                        f"[{overlay_idx}:v]format=rgba,scale={OUTPUT_W}:{OUTPUT_H}[overlay];"
                        f"{current}[overlay]overlay=0:0:format=auto[vout]"
                    )
                else:
                    dt = _build_drawtext_filters(
                        title or "", custom_text or "", y_title, y_text,
                        font_size_title=font_size_title, font_size_text=font_size_text,
                        title_color=title_color, text_color=text_color,
                    )
                    if dt:
                        filter_parts.append(f"{current}{dt}[vout]")
                    elif current != "[v0]":
                        filter_parts.append(f"{current}scale=iw:ih[vout]")
                    else:
                        filter_parts.append("[v0]scale=iw:ih[vout]")
            elif current != "[v0]":
                filter_parts.append(f"{current}scale=iw:ih[vout]")
            else:
                filter_parts.append("[v0]scale=iw:ih[vout]")
        else:
            # zoom_crop: marca d'água logo canto sup esquerdo (80% opacidade)
            if logo_path and logo_path.exists():
                inputs += ["-i", str(logo_path)]
                logo_idx = next_idx
                next_idx += 1
                filter_parts.append(
                    f"[{logo_idx}:v]scale=80:80:force_original_aspect_ratio=decrease,format=rgba,colorchannelmixer=aa=0.8[logo];"
                    f"{current}[logo]overlay=40:40:format=auto[v1]"
                )
                current = "[v1]"
            filter_parts.append(f"{current}scale=iw:ih[vout]")

        filter_complex = ";".join(filter_parts)

        if has_audio:
            cmd = [
                settings.FFMPEG_BIN, "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-map", "0:a",
                *video_encode_args(use_gpu),
                *audio_encode_args(input_path),
                *common_mp4_flags(),
                str(output_path),
            ]
        else:
            dur = ffprobe_duration(input_path)
            audio_idx = next_idx
            inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            filter_complex += f";[{audio_idx}:a]atrim=0:{dur},asetpts=PTS-STARTPTS[audio]"
            cmd = [
                settings.FFMPEG_BIN, "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-map", "[audio]",
                *video_encode_args(use_gpu),
                "-c:a", "aac", "-b:a", "160k",
                *common_mp4_flags(),
                str(output_path),
            ]

        res = run_cmd(cmd)
        if not res.ok:
            raise RuntimeError(f"vertical reformat failed: {res.stderr}\nfilter_complex={filter_complex}")
