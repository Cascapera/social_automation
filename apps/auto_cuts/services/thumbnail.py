from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)
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


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return max(0, bbox[2] - bbox[0])


def _split_word_to_width(draw: ImageDraw.ImageDraw, word: str, font, max_width: int) -> list[str]:
    if not word:
        return []
    chunks: list[str] = []
    current = ""
    for ch in word:
        candidate = f"{current}{ch}"
        if current and _text_width(draw, candidate, font) > max_width:
            chunks.append(current)
            current = ch
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = (text or "").strip().split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        if _text_width(draw, word, font) > max_width:
            pieces = _split_word_to_width(draw, word, font, max_width)
        else:
            pieces = [word]
        for piece in pieces:
            if not current:
                current = piece
                continue
            candidate = f"{current} {piece}"
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = piece
    if current:
        lines.append(current)
    return lines


def _fit_text_into_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    preferred_font: str,
    max_width: int,
    max_height: int,
    initial_font_size: int,
    min_font_size: int,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str], int]:
    font_size = max(min_font_size, initial_font_size)
    while font_size >= min_font_size:
        font = _safe_font(preferred_font, font_size)
        lines = _wrap_text(draw, text, font, max_width)
        if not lines:
            lines = [text]
        line_spacing = max(4, int(font_size * 0.2))
        line_heights = []
        for ln in lines:
            bbox = draw.textbbox((0, 0), ln, font=font)
            line_heights.append(max(1, bbox[3] - bbox[1]))
        text_h = sum(line_heights) + (len(lines) - 1) * line_spacing
        if text_h <= max_height:
            return font, lines, line_spacing
        font_size -= 2
    font = _safe_font(preferred_font, min_font_size)
    lines = _wrap_text(draw, text, font, max_width) or [text]
    line_spacing = max(4, int(min_font_size * 0.2))
    return font, lines, line_spacing


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


def generate_auto_thumbnail(corte, target_brand=None) -> bool:
    """
    Gera thumbnail automática usando timestamp sugerido + logo + texto inferior.
    target_brand: quando fornecido (roteamento por theme), usa logo e cores dessa brand.
    Retorna True se gerou, False se não conseguiu.
    """
    try:
        analysis = corte.analysis
        # Prioridade: target_brand (roteamento por tema) > analysis.brand
        # Quando target_brand existe, usar APENAS ela (nunca analysis) para evitar logo/cores da 1ª brand
        brand = target_brand or getattr(analysis, "brand", None)

        def _val(obj, attr, fallback):
            v = (getattr(obj, attr, None) or "").strip()
            return v if v else fallback

        if target_brand:
            selected_font = _val(target_brand, "thumbnail_font", "impact").lower()
            band_color = _hex_to_rgb(_val(target_brand, "thumbnail_band_color", "#E12E20"), (225, 46, 32))
            text_color = _hex_to_rgb(_val(target_brand, "thumbnail_text_color", "#0A0A0A"), (10, 10, 10))
            stroke_color = _hex_to_rgb(_val(target_brand, "thumbnail_effect_color", "#FFEBDC"), (255, 235, 220))
        else:
            selected_font = (
                _val(brand, "thumbnail_font", "") or _val(analysis, "thumbnail_font", "impact")
            ).strip().lower()
            band_color = _hex_to_rgb(
                _val(brand, "thumbnail_band_color", "") or _val(analysis, "thumbnail_band_color", "#E12E20"),
                (225, 46, 32),
            )
            text_color = _hex_to_rgb(
                _val(brand, "thumbnail_text_color", "") or _val(analysis, "thumbnail_text_color", "#0A0A0A"),
                (10, 10, 10),
            )
            stroke_color = _hex_to_rgb(
                _val(brand, "thumbnail_effect_color", "") or _val(analysis, "thumbnail_stroke_color", "#FFEBDC"),
                (255, 235, 220),
            )
        if selected_font not in {"anton", "bebas", "montserrat", "impact"}:
            selected_font = "impact"
        # Para Shorts (vertical): extrair frame do corte já extraído (formato 9:16, ideal para YouTube Shorts)
        # Para Longs (horizontal): usar vídeo original ou corte
        is_short = (getattr(corte, "format", "") or "").lower() == "vertical"
        if is_short and corte.file:
            try:
                corte_path = Path(corte.file.path)
                if corte_path.exists():
                    source_video_path = corte_path
                    ts_sec = 1.0  # frame no início do short (já é o corte)
                else:
                    source_video_path = None
                    ts_sec = 0.0
            except Exception:
                source_video_path = None
        else:
            source_video_path = None
        if not source_video_path:
            source_video = analysis.video_file
            if not source_video:
                return False
            source_video_path = Path(source_video.path)
            if not source_video_path.exists():
                return False
            raw = corte.suggestion.raw_data or {}
            ts_raw = raw.get("thumbnail_moment_timestamp") or raw.get("start_timestamp") or corte.suggestion.start_tc
            ts_sec = tc_to_seconds(str(ts_raw))
            start_sec = tc_to_seconds(corte.suggestion.start_tc or "")
            end_sec = tc_to_seconds(corte.suggestion.end_tc or "")
            if end_sec > start_sec and (ts_sec < start_sec or ts_sec > end_sec):
                ts_sec = start_sec + ((end_sec - start_sec) / 2.0)

        raw = corte.suggestion.raw_data or {}
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
            w, h = img.size
            # Shorts (9:16): se o frame for horizontal (16:9), recorta o centro para 9:16
            if is_short and w > h:
                new_w = int(h * 9 / 16)
                if new_w > 0 and new_w < w:
                    left = (w - new_w) // 2
                    img = img.crop((left, 0, left + new_w, h))
                    w, h = img.size
            draw = ImageDraw.Draw(img)

            # Logo topo-esquerda (se houver) - usa target_brand ou analysis.brand
            logo_asset = (
                BrandAsset.objects.filter(brand=brand, asset_type="LOGO")
                .order_by("id")
                .first()
                if brand and getattr(brand, "id", None)
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

            # Modelo de capa (Thumb Shorts ou Thumb Longs) - sobrepõe ao frame
            thumb_asset_type = "THUMB_SHORT" if is_short else "THUMB_LONG"
            thumb_asset = (
                BrandAsset.objects.filter(brand=brand, asset_type=thumb_asset_type)
                .order_by("id")
                .first()
                if brand and getattr(brand, "id", None)
                else None
            )
            has_thumb_model = thumb_asset and thumb_asset.file

            rect_h = max(1, int(h * 0.20))
            rect_w = w
            rect_x1 = 0
            rect_y1 = h - rect_h
            rect_x2 = rect_x1 + rect_w
            rect_y2 = h

            if has_thumb_model:
                try:
                    overlay_img = Image.open(thumb_asset.file.path).convert("RGBA")
                    overlay_resized = overlay_img.resize((w, h), Image.Resampling.LANCZOS)
                    img_rgba = img.convert("RGBA")
                    img = Image.alpha_composite(img_rgba, overlay_resized).convert("RGB")
                    draw = ImageDraw.Draw(img)
                except Exception as e:
                    logger.warning("[THUMB] Falha ao aplicar modelo %s: %s", thumb_asset_type, e)
                    has_thumb_model = False

            if not has_thumb_model:
                # Faixa inferior fixa em 20% da altura (fallback quando não há modelo)
                draw.rectangle([(rect_x1, rect_y1), (rect_x2, rect_y2)], fill=band_color)

            # Texto totalmente contido na faixa (quebra + redução de fonte).
            text_padding_x = max(20, int(w * 0.03))
            text_padding_y = max(12, int(rect_h * 0.12))
            text_max_width = max(120, w - (2 * text_padding_x))
            text_max_height = max(24, rect_h - (2 * text_padding_y))
            initial_font_size = max(26, int(w * 0.065))
            min_font_size = max(14, int(w * 0.022))
            font, lines, line_spacing = _fit_text_into_box(
                draw=draw,
                text=thumb_text,
                preferred_font=selected_font,
                max_width=text_max_width,
                max_height=text_max_height,
                initial_font_size=initial_font_size,
                min_font_size=min_font_size,
            )

            line_heights = []
            for ln in lines:
                bbox = draw.textbbox((0, 0), ln, font=font)
                line_heights.append(max(1, bbox[3] - bbox[1]))
            text_block_h = sum(line_heights) + (len(lines) - 1) * line_spacing
            cursor_y = rect_y1 + max(0, (rect_h - text_block_h) // 2)
            stroke_width = max(1, int(getattr(font, "size", min_font_size) * 0.08))
            for ln, ln_h in zip(lines, line_heights):
                ln_w = _text_width(draw, ln, font)
                tx = (w - ln_w) // 2
                draw.text(
                    (tx, cursor_y),
                    ln,
                    font=font,
                    fill=text_color,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_color,
                )
                cursor_y += ln_h + line_spacing

            img.save(out_path, format="JPEG", quality=92, optimize=True)
            # YouTube limita thumbnail a 2MB; reduz qualidade se necessário.
            YT_THUMB_MAX_BYTES = 2 * 1024 * 1024
            if out_path.stat().st_size > YT_THUMB_MAX_BYTES:
                for q in (85, 75, 65):
                    img.save(out_path, format="JPEG", quality=q, optimize=True)
                    if out_path.stat().st_size <= YT_THUMB_MAX_BYTES:
                        break

            # Substitui thumbnail antiga, se existir.
            try:
                if corte.thumbnail:
                    corte.thumbnail.delete(save=False)
            except Exception:
                pass
            with open(out_path, "rb") as f:
                corte.thumbnail.save(f"autocut_{corte.id}.jpg", File(f), save=True)
            return True
    except Exception as e:
        logger.warning("[THUMB] Falha ao gerar thumbnail para corte %s: %s", getattr(corte, "id", "?"), e)
        return False
