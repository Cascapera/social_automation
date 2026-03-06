from __future__ import annotations

import tempfile
import re
from pathlib import Path

from django.conf import settings
from django.core.files import File

from PIL import Image, ImageDraw, ImageFont

from apps.brands.models import BrandAsset
from apps.jobs.services.ffmpeg import run_cmd, tc_to_seconds

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _safe_font(preferred_font: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # 4 opções fixas configuradas no app.
    font_map = {
        "anton": ["Anton-Regular.ttf", "anton.ttf"],
        "bebas": ["BebasNeue-Regular.ttf", "bebasneue.ttf"],
        "montserrat": ["Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf", "montserrat-extrabold.ttf"],
        "impact": ["impact.ttf", "Impact.ttf"],
    }
    candidates = list(font_map.get(preferred_font, [])) + [
        "arialbd.ttf",
        "arial.ttf",
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
    ]
    fonts_dir = Path(settings.MEDIA_ROOT) / "fonts"
    for font_name in list(candidates):
        candidates.append(str(fonts_dir / font_name))
    for font_name in candidates:
        try:
            return ImageFont.truetype(font_name, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = (text or "").strip().split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:2]


def _extract_frame_at(video_path: Path, output_image_path: Path, sec: float) -> None:
    ts = max(0.0, float(sec))
    cmd = [
        settings.FFMPEG_BIN,
        "-y",
        "-ss",
        f"{ts:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_image_path),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"frame extract failed: {res.stderr}")


def _hex_to_rgb(hex_color: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if not hex_color:
        return fallback
    value = str(hex_color).strip()
    if not HEX_COLOR_RE.match(value):
        return fallback
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def generate_auto_thumbnail(corte) -> bool:
    """
    Gera thumbnail automática usando timestamp sugerido + logo + texto inferior.
    Retorna True se gerou, False se não conseguiu.
    """
    try:
        analysis = corte.analysis
        selected_font = (getattr(analysis, "thumbnail_font", "") or "impact").strip().lower()
        if selected_font not in {"anton", "bebas", "montserrat", "impact"}:
            selected_font = "impact"
        band_color = _hex_to_rgb(getattr(analysis, "thumbnail_band_color", "#E12E20"), (225, 46, 32))
        text_color = _hex_to_rgb(getattr(analysis, "thumbnail_text_color", "#0A0A0A"), (10, 10, 10))
        stroke_color = _hex_to_rgb(getattr(analysis, "thumbnail_stroke_color", "#FFEBDC"), (255, 235, 220))
        source_video = analysis.video_file
        if not source_video:
            return False
        source_video_path = Path(source_video.path)
        if not source_video_path.exists():
            return False

        raw = corte.suggestion.raw_data or {}
        ts_raw = raw.get("thumbnail_moment_timestamp") or raw.get("start_timestamp") or corte.suggestion.start_tc
        ts_sec = tc_to_seconds(str(ts_raw))

        # Garante que o frame esteja dentro do intervalo do corte quando possível.
        start_sec = tc_to_seconds(corte.suggestion.start_tc or "")
        end_sec = tc_to_seconds(corte.suggestion.end_tc or "")
        if end_sec > start_sec:
            if ts_sec < start_sec or ts_sec > end_sec:
                ts_sec = start_sec + ((end_sec - start_sec) / 2.0)

        thumb_text = (raw.get("thumbnail_text") or "").strip()
        if not thumb_text:
            fallback = (raw.get("suggested_title") or corte.suggestion.title or "").strip()
            thumb_text = " ".join(fallback.split()[:4]).upper()[:28] or "DESTAQUE"
        thumb_text = thumb_text.upper()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            frame_path = tmpdir_path / "frame.jpg"
            out_path = tmpdir_path / "thumb_final.jpg"
            _extract_frame_at(source_video_path, frame_path, ts_sec)

            img = Image.open(frame_path).convert("RGB")
            draw = ImageDraw.Draw(img)
            w, h = img.size

            # Logo topo-esquerda (se houver).
            logo_asset = (
                BrandAsset.objects.filter(brand=analysis.brand, asset_type="LOGO")
                .order_by("id")
                .first()
                if analysis.brand_id
                else None
            )
            margin = max(16, int(w * 0.02))
            if logo_asset and logo_asset.file:
                try:
                    logo = Image.open(logo_asset.file.path).convert("RGBA")
                    max_logo_w = int(w * 0.22)
                    max_logo_h = int(h * 0.22)
                    logo.thumbnail((max_logo_w, max_logo_h), Image.Resampling.LANCZOS)
                    img.paste(logo, (margin, margin), logo)
                except Exception:
                    pass

            # Texto inferior em faixa contínua (full width) com quebra automática.
            font_size = max(34, int(w * 0.06))
            font = _safe_font(selected_font, font_size)
            text_padding_x = max(20, int(w * 0.02))
            text_padding_y = max(14, int(h * 0.015))
            text_max_width = max(120, w - (2 * text_padding_x))
            lines = _wrap_text(draw, thumb_text, font, text_max_width)
            if not lines:
                lines = [thumb_text]

            line_heights = [draw.textbbox((0, 0), ln, font=font)[3] for ln in lines]
            text_block_h = sum(line_heights) + (len(lines) - 1) * int(font_size * 0.2)
            rect_h = text_block_h + (2 * text_padding_y)
            rect_w = w
            rect_x1 = 0
            rect_y1 = h - rect_h - margin
            rect_x2 = rect_x1 + rect_w
            rect_y2 = rect_y1 + rect_h

            draw.rectangle([(rect_x1, rect_y1), (rect_x2, rect_y2)], fill=band_color)

            cursor_y = rect_y1 + text_padding_y
            stroke_width = max(2, int(font_size * 0.08))
            for ln, ln_h in zip(lines, line_heights):
                ln_w = draw.textbbox((0, 0), ln, font=font)[2]
                tx = (w - ln_w) // 2
                draw.text(
                    (tx, cursor_y),
                    ln,
                    font=font,
                    fill=text_color,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_color,
                )
                cursor_y += ln_h + int(font_size * 0.2)

            img.save(out_path, format="JPEG", quality=92, optimize=True)

            # Substitui thumbnail antiga, se existir.
            try:
                if corte.thumbnail:
                    corte.thumbnail.delete(save=False)
            except Exception:
                pass
            with open(out_path, "rb") as f:
                corte.thumbnail.save(f"autocut_{corte.id}.jpg", File(f), save=True)
            return True
    except Exception:
        return False
