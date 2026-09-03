from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File
from PIL import Image, ImageDraw, ImageFont

from apps.brands.models import BrandAsset
from apps.jobs.services.ffmpeg import (
    ffprobe_duration,
    resilient_decode_options,
    resilient_input_demuxer_flags,
    run_cmd,
    tc_to_seconds,
)

logger = logging.getLogger(__name__)

# Se o timestamp sugerido falhar (FFmpeg), usar este instante no ficheiro de vídeo do corte (relativo ou absoluto conforme o caso).
THUMB_FALLBACK_SEC_IN_CUT = 5.0

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# YouTube limita thumbnail a 2MB.
YT_THUMB_MAX_BYTES = 2 * 1024 * 1024


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
    *,
    absolute_min_font_size: int = 8,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str], int]:
    floor = max(8, absolute_min_font_size)
    font_size = max(min_font_size, initial_font_size)
    while font_size >= floor:
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
    font = _safe_font(preferred_font, floor)
    lines = _wrap_text(draw, text, font, max_width) or [text]
    line_spacing = max(4, int(floor * 0.2))
    return font, lines, line_spacing


def _block_metrics(font, n_lines: int) -> tuple[int, int, int]:
    """Altura de linha, espaçamento e altura do bloco, pela métrica da fonte.

    Medir por `ascent + descent` (e não pelo bbox da tinta) mantém o espaçamento uniforme:
    medindo a tinta, uma linha sem acento nem descendente fica mais baixa que a de cima e as
    linhas "pulam".
    """
    try:
        ascent, descent = font.getmetrics()
        line_h = max(1, int(ascent + descent))
    except (AttributeError, OSError):
        line_h = max(1, int(getattr(font, "size", 16) * 1.2))
    spacing = max(2, int(line_h * 0.12))
    n = max(1, n_lines)
    return line_h, spacing, (n * line_h) + ((n - 1) * spacing)


def _fit_text_into_zone(
    draw: ImageDraw.ImageDraw,
    text: str,
    preferred_font: str,
    max_width: int,
    max_height: int,
    initial_font_size: int,
    min_font_size: int,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str], int, int, int]:
    """Maior corpo de fonte que faz o texto caber na zona, quebrando em linhas.

    Irmão de `_fit_text_into_box` (usado no fallback da faixa inferior), mas medindo pela
    métrica da fonte — ver `_block_metrics`.
    """
    words = (text or "").split()
    floor = max(8, min_font_size)
    font_size = max(floor, initial_font_size)
    while True:
        font = _safe_font(preferred_font, font_size)
        lines = _wrap_text(draw, text, font, max_width) or [text]
        line_h, spacing, block_h = _block_metrics(font, len(lines))
        # Palavra mais larga que a zona seria partida ao meio ("classificaç/ão"); antes disso,
        # vale a pena encolher a fonte — só se parte palavra no corpo mínimo.
        widest_word = max((_text_width(draw, w, font) for w in words), default=0)
        if (block_h <= max_height and widest_word <= max_width) or font_size <= floor:
            return font, lines, line_h, spacing, block_h
        font_size -= 2


def _resolve_thumb_template(analysis, brand, is_short: bool):
    """Modelo de capa a usar neste corte, ou None (= faixa inferior).

    Ordem: escolha do job > padrão da marca > primeiro asset da marca.

    O último degrau é compatibilidade: marcas que já usam modelo hoje (quando a geração pegava
    `.order_by("id").first()`) continuam com o mesmo modelo sem ter de configurar nada.

    A escolha do job só vale se o asset for da marca *do corte*: com roteamento por tema, o
    corte da marca B não pode sair com a arte da marca A.
    """
    brand_id = getattr(brand, "id", None)
    if not brand_id:
        return None
    asset_type = "THUMB_SHORT" if is_short else "THUMB_LONG"
    suffix = "short" if is_short else "long"

    def _usable(asset):
        return (
            asset
            and asset.brand_id == brand_id
            and asset.asset_type == asset_type
            and asset.file
        )

    for candidate in (
        getattr(analysis, f"thumb_template_{suffix}", None),
        getattr(brand, f"default_thumb_template_{suffix}", None),
    ):
        if _usable(candidate):
            return candidate
    return (
        BrandAsset.objects.filter(brand_id=brand_id, asset_type=asset_type)
        .order_by("id")
        .first()
    )


def _resolve_text_zone(w: int, h: int, template) -> tuple[int, int, int, int]:
    """Caixa de texto do modelo (percentagens → pixels), sempre dentro da imagem."""

    def _pct(attr: str, fallback: int) -> int:
        try:
            value = int(getattr(template, attr, fallback))
        except (TypeError, ValueError):
            value = fallback
        return max(0, min(100, value))

    x1 = int(w * _pct("text_zone_x", 60) / 100)
    y1 = int(h * _pct("text_zone_y", 8) / 100)
    x2 = min(w, x1 + int(w * _pct("text_zone_w", 38) / 100))
    y2 = min(h, y1 + int(h * _pct("text_zone_h", 84) / 100))
    if (x2 - x1) < 16 or (y2 - y1) < 16:
        # Zona inutilizável (mal configurada): melhor a imagem inteira do que perder o texto.
        return 0, 0, w, h
    return x1, y1, x2, y2


def _line_ink_bbox(draw: ImageDraw.ImageDraw, line: str, font, stroke_width: int):
    """Caixa da tinta de uma linha desenhada com âncora `la` na origem (contorno incluído)."""
    try:
        return draw.textbbox((0, 0), line, font=font, anchor="la", stroke_width=stroke_width)
    except (ValueError, TypeError):
        # Fonte bitmap (load_default): sem âncora.
        return draw.textbbox((0, 0), line, font=font)


def _ink_extents(draw, lines, font, line_h: int, spacing: int, stroke_width: int) -> tuple[int, int]:
    """Topo e base da tinta do bloco, relativos ao y da primeira linha.

    A métrica da fonte diz onde ficam as linhas; a tinta pode passar disso — um acento
    ultrapassa o ascendente, um `ç` ultrapassa a base. É pela tinta que a zona é garantida.
    """
    top: int | None = None
    bottom: int | None = None
    for index, line in enumerate(lines):
        offset = index * (line_h + spacing)
        bbox = _line_ink_bbox(draw, line, font, stroke_width)
        top = bbox[1] + offset if top is None else min(top, bbox[1] + offset)
        bottom = bbox[3] + offset if bottom is None else max(bottom, bbox[3] + offset)
    if top is None or bottom is None:
        return 0, 0
    return top, bottom


def _draw_text_in_zone(
    draw: ImageDraw.ImageDraw,
    text: str,
    zone: tuple[int, int, int, int],
    *,
    preferred_font: str,
    text_color: tuple[int, int, int],
    stroke_color: tuple[int, int, int],
    stroke_enabled: bool,
    align: str,
    valign: str,
) -> None:
    """Escreve o texto dentro da zona, sem deixar tinta escapar dela."""
    x1, y1, x2, y2 = zone
    zone_w = x2 - x1
    zone_h = y2 - y1
    # Folga de 2%: sem ela a tinta encosta na borda da arte.
    inset = max(2, int(min(zone_w, zone_h) * 0.02))
    left = x1 + inset
    right = x2 - inset
    top = y1 + inset
    bottom = y2 - inset
    box_w = max(16, right - left)
    box_h = max(16, bottom - top)
    initial_font_size = max(16, int(box_w * 0.22))
    min_font_size = max(12, int(box_w * 0.06))

    max_width = box_w
    max_height = box_h
    font, lines, line_h, spacing, _ = _fit_text_into_zone(
        draw, text, preferred_font, max_width, max_height, initial_font_size, min_font_size
    )
    stroke_width = max(1, int(getattr(font, "size", min_font_size) * 0.08)) if stroke_enabled else 0

    # A métrica subestima a tinta: acentos passam do ascendente, `ç` passa da base e o contorno
    # engrossa tudo para os lados. Encolhe pela tinta medida até caber mesmo.
    for _ in range(6):
        ink_top, ink_bottom = _ink_extents(draw, lines, font, line_h, spacing, stroke_width)
        widest = max(
            (
                bbox[2] - bbox[0]
                for bbox in (_line_ink_bbox(draw, ln, font, stroke_width) for ln in lines)
            ),
            default=0,
        )
        overflow_h = (ink_bottom - ink_top) - box_h
        overflow_w = widest - box_w
        at_floor = getattr(font, "size", min_font_size) <= min_font_size
        if (overflow_h <= 0 and overflow_w <= 0) or at_floor:
            break
        max_height = max(16, max_height - max(0, overflow_h))
        max_width = max(16, max_width - max(0, overflow_w))
        font, lines, line_h, spacing, _ = _fit_text_into_zone(
            draw, text, preferred_font, max_width, max_height, initial_font_size, min_font_size
        )
        if stroke_enabled:
            stroke_width = max(1, int(getattr(font, "size", min_font_size) * 0.08))

    ink_top, ink_bottom = _ink_extents(draw, lines, font, line_h, spacing, stroke_width)
    ink_h = ink_bottom - ink_top
    if valign == "top":
        desired_ink_top = top
    elif valign == "bottom":
        desired_ink_top = bottom - ink_h
    else:
        desired_ink_top = top + max(0, (box_h - ink_h) // 2)
    cursor_y = desired_ink_top - ink_top

    for index, line in enumerate(lines):
        bbox = _line_ink_bbox(draw, line, font, stroke_width)
        line_ink_w = bbox[2] - bbox[0]
        if align == "left":
            desired_ink_left = left
        elif align == "right":
            desired_ink_left = right - line_ink_w
        else:
            desired_ink_left = left + max(0, (box_w - line_ink_w) // 2)
        draw.text(
            (desired_ink_left - bbox[0], cursor_y + index * (line_h + spacing)),
            line,
            font=font,
            fill=text_color,
            anchor="la",  # topo do ascendente em y: baselines regulares entre as linhas
            stroke_width=stroke_width,
            stroke_fill=stroke_color,
        )


def _save_thumbnail(corte, img: Image.Image, out_path: Path) -> None:
    """Grava a capa no corte, respeitando o limite de 2 MB do YouTube."""
    img.save(out_path, format="JPEG", quality=92, optimize=True)
    if out_path.stat().st_size > YT_THUMB_MAX_BYTES:
        for quality in (85, 75, 65):
            img.save(out_path, format="JPEG", quality=quality, optimize=True)
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


def _extract_frame_at(video_path: Path, output_image_path: Path, sec: float) -> None:
    """
    Extrai um frame como PNG. Evita encoder MJPEG (.jpg), que em alguns builds/ffmpeg
    falha (ff_frame_thread_encoder_init / dimensões ímpares) ao gerar a capa.
    """
    ts = max(0.0, float(sec))
    cmd = [
        settings.FFMPEG_BIN,
        "-y",
        *resilient_decode_options(),
        "-ss",
        f"{ts:.3f}",
        *resilient_input_demuxer_flags(),
        "-i",
        str(video_path),
        "-an",
        "-sn",
        "-dn",
        "-frames:v",
        "1",
        "-vcodec",
        "png",
        str(output_image_path),
    ]
    res = run_cmd(cmd)
    if not res.ok:
        raise RuntimeError(f"frame extract failed: {res.stderr}")
    if not output_image_path.exists() or output_image_path.stat().st_size < 32:
        raise RuntimeError(f"frame extract produced no output: {output_image_path}")


def _extract_frame_with_fallback(
    video_path: Path,
    output_image_path: Path,
    primary_sec: float,
    fallback_sec: float,
) -> None:
    """Tenta o instante principal; em erro, tenta fallback (ex. 5 s dentro do corte)."""
    try:
        _extract_frame_at(video_path, output_image_path, primary_sec)
    except Exception as e:
        logger.warning(
            "[THUMB] Frame em %.2fs falhou (%s); a usar fallback %.2fs",
            primary_sec,
            e,
            fallback_sec,
        )
        _extract_frame_at(video_path, output_image_path, fallback_sec)


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
        sug_start_sec = tc_to_seconds(corte.suggestion.start_tc or "")
        sug_end_sec = tc_to_seconds(corte.suggestion.end_tc or "")
        # True = ficheiro é só o corte (timeline 0..duração); False = vídeo fonte completo (timestamps absolutos).
        timeline_is_cut_only = False
        # Para Shorts (vertical): extrair frame do corte já extraído (formato 9:16, ideal para YouTube Shorts)
        # Para Longs (horizontal): corte final (ex.: concat em cortes prontos sem vídeo na análise) ou vídeo original
        is_short = (getattr(corte, "format", "") or "").lower() == "vertical"
        raw_early = corte.suggestion.raw_data or {}
        if is_short and corte.file:
            try:
                corte_path = Path(corte.file.path)
                if corte_path.exists():
                    source_video_path = corte_path
                    timeline_is_cut_only = True
                    # Cortes prontos: frame nos primeiros ~5s (padrão 2s); fallback 1s
                    ts_sec = float(raw_early.get("thumbnail_frame_sec", 1.0))
                    if ts_sec < 0:
                        ts_sec = 1.0
                else:
                    source_video_path = None
                    ts_sec = 0.0
            except Exception:
                source_video_path = None
        elif not is_short and corte.file:
            try:
                corte_path = Path(corte.file.path)
                if corte_path.exists():
                    source_video_path = corte_path
                    timeline_is_cut_only = True
                    raw_h = corte.suggestion.raw_data or {}
                    ts_raw = raw_h.get("thumbnail_moment_timestamp") or raw_h.get("start_timestamp") or corte.suggestion.start_tc
                    ts_sec = tc_to_seconds(str(ts_raw))
                    start_sec = tc_to_seconds(corte.suggestion.start_tc or "")
                    end_sec = tc_to_seconds(corte.suggestion.end_tc or "")
                    if end_sec > start_sec and (ts_sec < start_sec or ts_sec > end_sec):
                        ts_sec = start_sec + ((end_sec - start_sec) / 2.0)
                    # Ficheiro extraído começa em 0:00; a LLM devolve tempo absoluto no vídeo original.
                    # Sem isto, o seek aponta para além da duração (ex. 938 s num MP4 de ~12 min) e não há frame.png.
                    if end_sec > start_sec:
                        ts_sec = ts_sec - start_sec
                        cut_len = end_sec - start_sec
                        ts_sec = max(0.0, min(ts_sec, cut_len - 0.25))
                    else:
                        ts_sec = 0.0
                else:
                    source_video_path = None
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
            start_sec = sug_start_sec
            end_sec = sug_end_sec
            if end_sec > start_sec and (ts_sec < start_sec or ts_sec > end_sec):
                ts_sec = start_sec + ((end_sec - start_sec) / 2.0)

        def _thumb_fallback_seek_sec() -> float:
            """~5 s dentro do corte: relativo ao ficheiro se for só o corte; senão absoluto no vídeo fonte."""
            try:
                dur = ffprobe_duration(source_video_path)
            except Exception:
                dur = 0.0
            if timeline_is_cut_only:
                return min(THUMB_FALLBACK_SEC_IN_CUT, max(0.0, dur - 0.25))
            if sug_end_sec > sug_start_sec:
                cut_len = sug_end_sec - sug_start_sec
                return sug_start_sec + min(THUMB_FALLBACK_SEC_IN_CUT, max(0.0, cut_len - 0.25))
            return min(THUMB_FALLBACK_SEC_IN_CUT, max(0.0, dur - 0.25))

        raw = corte.suggestion.raw_data or {}
        if is_short:
            thumb_text = (raw.get("thumbnail_text") or "").strip()
            if not thumb_text:
                fallback = (raw.get("suggested_title") or corte.suggestion.title or "").strip()
                thumb_text = " ".join(fallback.split()[:4]).upper()[:28] or "DESTAQUE"
            thumb_text = thumb_text.upper()
        else:
            # Vídeo longo (16:9): texto da capa = título completo, na faixa inferior (~20%).
            thumb_text = (
                (raw.get("suggested_title") or raw.get("title_suggestion") or corte.suggestion.title or "")
                .strip()
            )
            if not thumb_text:
                thumb_text = (raw.get("thumbnail_text") or "").strip()
            if not thumb_text:
                thumb_text = "DESTAQUE"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            frame_path = tmpdir_path / "frame.png"
            out_path = tmpdir_path / "thumb_final.jpg"
            _extract_frame_with_fallback(
                source_video_path,
                frame_path,
                ts_sec,
                _thumb_fallback_seek_sec(),
            )

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

            # Modelo de capa (Thumb Shorts ou Thumb Longs) - sobrepõe ao frame
            template = _resolve_thumb_template(analysis, brand, is_short)
            has_thumb_model = bool(template and template.file)

            if has_thumb_model:
                try:
                    overlay_img = Image.open(template.file.path).convert("RGBA")
                    overlay_resized = overlay_img.resize((w, h), Image.Resampling.LANCZOS)
                    img_rgba = img.convert("RGBA")
                    img = Image.alpha_composite(img_rgba, overlay_resized).convert("RGB")
                    draw = ImageDraw.Draw(img)
                except Exception as e:
                    logger.warning("[THUMB] Failed to apply template %s: %s", template.asset_type, e)
                    has_thumb_model = False

            if has_thumb_model:
                # Texto na zona configurada no modelo (por omissão, a lateral direita).
                # Sem logo: a arte do modelo já é a identidade da marca — e, sendo opaca, taparia o logo.
                _draw_text_in_zone(
                    draw,
                    thumb_text,
                    _resolve_text_zone(w, h, template),
                    preferred_font=(template.font or "").strip().lower() or selected_font,
                    text_color=_hex_to_rgb(template.text_color, text_color),
                    stroke_color=_hex_to_rgb(template.stroke_color, stroke_color),
                    stroke_enabled=bool(template.stroke_enabled),
                    align=(template.text_align or "center"),
                    valign=(template.text_valign or "middle"),
                )
                _save_thumbnail(corte, img, out_path)
                return True

            # Sem modelo: logo no canto superior esquerdo + faixa inferior com o texto centrado.
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

            rect_h = max(1, int(h * 0.20))
            rect_w = w
            rect_x1 = 0
            rect_y1 = h - rect_h
            rect_x2 = rect_x1 + rect_w
            rect_y2 = h
            draw.rectangle([(rect_x1, rect_y1), (rect_x2, rect_y2)], fill=band_color)

            # Texto totalmente contido na faixa (quebra + redução de fonte).
            text_padding_x = max(20, int(w * 0.03))
            text_padding_y = max(12, int(rect_h * 0.12))
            text_max_width = max(120, w - (2 * text_padding_x))
            text_max_height = max(24, rect_h - (2 * text_padding_y))
            if is_short:
                initial_font_size = max(26, int(w * 0.065))
                min_font_size = max(14, int(w * 0.022))
            else:
                # Título longo: começar um pouco menor e permitir reduzir mais para caber na faixa.
                initial_font_size = max(22, min(int(w * 0.05), int(rect_h * 0.38)))
                min_font_size = max(10, int(w * 0.014))
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
            for ln, ln_h in zip(lines, line_heights, strict=True):
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

            _save_thumbnail(corte, img, out_path)
            return True
    except Exception as e:
        logger.warning("[THUMB] Failed to generate thumbnail for cut %s: %s", getattr(corte, "id", "?"), e)
        return False
