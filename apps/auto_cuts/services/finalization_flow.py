"""Fluxo de finalização de auto_cuts (refactor.md R-19 / D-11).

Destino do corpo de `finalizar_auto_cut_task`, que tinha 556 linhas e 20 parâmetros dentro
de `apps/auto_cuts/tasks.py`. A task Celery continua lá — o nome dela é contrato de fila —,
mas agora ela só delega para `run_finalization()`.

O fluxo está fatiado assim:

  `run_finalization`        estado de entrada, limpeza, laço dos cortes, estado de saída
  `_build_options`          os 20 parâmetros viram um `FinalizeOptions` já normalizado
  `_delete_unselected_cuts` a parte destrutiva: apaga o que o usuário desmarcou
  `_process_cut`            um corte: guardas, render, `is_finalized`, inventário
  `_render_cut`             o pipeline de vídeo, na ordem em que roda
  `_ensure_vertical_format` · `_ensure_long_canvas` · `_apply_overlay_animation` ·
  `_apply_long_side_overlay` · `_apply_long_logo` · `_burn_cut_subtitles`

⚠ **Falha de corte não vira `status="error"`.** A análise fica parada em `finalizing` com
uma mensagem própria, porque existe um fluxo de recovery que procura exatamente esse estado
(`services/recovery.py`). "Melhorar" isso para `error` quebra o recovery em silêncio — o
`test_finalize_task_characterization.py` existe para impedir.

Cada etapa de render engole a própria exceção e devolve `step_failed=True`: uma etapa que
falha não aborta as seguintes, mas impede o corte de ser marcado como finalizado. Era assim
na função original e continua sendo.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte
from apps.auto_cuts.services.flow_common import (
    _mark_analysis_done,
    _resolve_target_brand_for_suggestion,
    _safe_save_analysis,
    _sanitize_long_overlay_fk,
    _sync_inventory_item_from_corte,
)
from apps.auto_cuts.services.vertical_reformat import reformat_video_vertical
from apps.brands.models import BrandAsset
from apps.common.metrics import (
    render_duration_ms,
    render_failures_total,
    render_jobs_total,
)
from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.services.ffmpeg import (
    ffprobe_sample_aspect_ratio_float,
    ffprobe_video_info,
    has_nvenc,
    normalize_video_to_canvas,
    overlay_animation,
    overlay_logo,
    overlay_long_right,
)
from apps.jobs.services.subtitles import burn_subtitles, segments_to_srt

logger = logging.getLogger(__name__)

# Default subtitle style (emoji-capable font).
# size = ASS FontSize in PlayRes units (≈ px relative to video height).
_SUBTITLE_STYLE_BASE = {
    "font": "Segoe UI Emoji",
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline": 2,
}
# Shorts (9:16): default 10 px; 16:9 longs keep previous default (36) when user omits "size".
DEFAULT_SUBTITLE_STYLE_SHORT = {**_SUBTITLE_STYLE_BASE, "size": 10}
DEFAULT_SUBTITLE_STYLE_LONG = {**_SUBTITLE_STYLE_BASE, "size": 36}
DEFAULT_SUBTITLE_STYLE = DEFAULT_SUBTITLE_STYLE_LONG


@dataclass(frozen=True)
class FinalizeOptions:
    """Os 20 parâmetros da task, já com default aplicado e valor preso na faixa.

    Existe para o pipeline de render receber uma coisa só em vez de 16 argumentos soltos.
    A normalização acontece uma vez, em `_build_options`, e não se repete por corte.
    """

    subtitle_style: dict
    vertical_mode: str
    background_color: str
    custom_text: str
    font_size_title: int
    font_size_text: int
    title_color: str
    text_color: str
    logo_x: int
    logo_y: int
    overlay_animation_asset_id: int | None
    overlay_position: str
    overlay_margin: int
    overlay_height: int
    long_overlay_enabled: bool
    long_overlay_asset_id: int | None


def _build_options(
    analysis,
    *,
    subtitle_style: dict | None,
    vertical_mode: str | None,
    background_color: str | None,
    custom_text: str | None,
    font_size_title: int | None,
    font_size_text: int | None,
    title_color: str | None,
    text_color: str | None,
    horizontal_logo_x: int | None,
    horizontal_logo_y: int | None,
    overlay_animation_asset_id: int | None,
    overlay_position: str | None,
    overlay_margin: int | None,
    overlay_height: int | None,
    long_overlay_enabled: bool | None,
    long_overlay_asset_id: int | None,
) -> FinalizeOptions:
    """Aplica default e faixa. `None` no overlay longo significa "usa o que está no job"."""
    if long_overlay_enabled is None:
        lo_enabled = bool(getattr(analysis, "long_overlay_enabled", False))
    else:
        lo_enabled = bool(long_overlay_enabled)
    if long_overlay_asset_id is None:
        lo_asset_id = getattr(analysis, "long_overlay_asset_id", None)
    else:
        lo_asset_id = int(long_overlay_asset_id) if long_overlay_asset_id else None

    return FinalizeOptions(
        subtitle_style=subtitle_style or {},
        vertical_mode=vertical_mode or "zoom_crop",
        background_color=(background_color or "#000000").strip(),
        custom_text=(custom_text or "").strip(),
        font_size_title=36 if font_size_title is None else max(12, min(96, int(font_size_title))),
        font_size_text=28 if font_size_text is None else max(12, min(72, int(font_size_text))),
        title_color=(title_color or "#FFFFFF").strip(),
        text_color=(text_color or "#FFFFFF").strip(),
        # Logo as watermark: top-left, 40px margin, 80% opacity
        logo_x=max(0, min(2000, int(horizontal_logo_x or 40))),
        logo_y=max(0, min(1200, int(horizontal_logo_y or 40))),
        overlay_animation_asset_id=overlay_animation_asset_id,
        overlay_position=(overlay_position or "bottom_right").strip() or "bottom_right",
        overlay_margin=max(0, min(100, int(overlay_margin or 24))),
        overlay_height=max(20, min(400, int(overlay_height or 120))),
        long_overlay_enabled=lo_enabled,
        long_overlay_asset_id=lo_asset_id,
    )


def _logo_path_for_brand(brand):
    """Return brand logo Path or None."""
    if not brand or not getattr(brand, "id", None):
        return None
    logo_asset = BrandAsset.objects.filter(
        brand_id=brand.id, asset_type="LOGO"
    ).first()
    if logo_asset and logo_asset.file:
        try:
            return Path(logo_asset.file.path)
        except Exception:
            pass
    return None


def _animation_path_for_brand(brand, asset_id):
    """Return brand overlay animation Path or None."""
    if not brand or not asset_id:
        return None
    anim_asset = BrandAsset.objects.filter(
        id=asset_id,
        brand_id=brand.id,
        asset_type="ANIMATION",
    ).first()
    if anim_asset and anim_asset.file:
        try:
            return Path(anim_asset.file.path)
        except Exception:
            pass
    return None


def _long_overlay_path_for_brand(brand, asset_id):
    """Return brand side overlay (long video) Path or None."""
    if not brand or not asset_id:
        return None
    ovl = BrandAsset.objects.filter(
        id=asset_id,
        brand_id=brand.id,
        asset_type="OVERLAY_LONG",
    ).first()
    if ovl and ovl.file:
        try:
            return Path(ovl.file.path)
        except Exception:
            pass
    return None


def run_finalization(
    task,
    analysis_id: int,
    *,
    subtitle_style: dict | None = None,
    vertical_mode: str | None = None,
    background_color: str | None = None,
    custom_text: str | None = None,
    font_size_title: int | None = None,
    font_size_text: int | None = None,
    title_color: str | None = None,
    text_color: str | None = None,
    horizontal_logo_x: int | None = None,
    horizontal_logo_y: int | None = None,
    overlay_animation_asset_id: int | None = None,
    overlay_position: str | None = None,
    overlay_margin: int | None = None,
    overlay_height: int | None = None,
    long_overlay_enabled: bool | None = None,
    long_overlay_asset_id: int | None = None,
) -> None:
    """Corpo de `finalizar_auto_cut_task`.

    Apaga os cortes não selecionados, renderiza os selecionados e sincroniza o inventário.
    `task` é o `self` da task Celery (`bind=True`), usado só para o `task_id` do log.

    Nada a finalizar é **sucesso**: a análise vai para `done`. Sem isso, um job em que o
    usuário desmarcou tudo ficaria preso em `finalizing` para sempre.
    """
    try:
        analysis = AutoCutAnalysis.objects.get(id=analysis_id)
    except ObjectDoesNotExist:
        return

    if _sanitize_long_overlay_fk(analysis):
        analysis.save(update_fields=["long_overlay_asset_id", "long_overlay_enabled"])
        logger.warning(
            "[FLUXO] Analysis %s: orphan side overlay during finalize; disabled.",
            analysis_id,
        )

    analysis.status = "finalizing"
    analysis.progress_message = "Finalizando cortes e sincronizando inventário..."
    # O progresso nunca retrocede: uma análise que chegou com 98 não volta para 95.
    analysis.progress = min(99, max(int(getattr(analysis, "progress", 0) or 0), 95))
    analysis.error = ""
    if not _safe_save_analysis(analysis, ["status", "progress_message", "progress", "error"]):
        return

    opts = _build_options(
        analysis,
        subtitle_style=subtitle_style,
        vertical_mode=vertical_mode,
        background_color=background_color,
        custom_text=custom_text,
        font_size_title=font_size_title,
        font_size_text=font_size_text,
        title_color=title_color,
        text_color=text_color,
        horizontal_logo_x=horizontal_logo_x,
        horizontal_logo_y=horizontal_logo_y,
        overlay_animation_asset_id=overlay_animation_asset_id,
        overlay_position=overlay_position,
        overlay_margin=overlay_margin,
        overlay_height=overlay_height,
        long_overlay_enabled=long_overlay_enabled,
        long_overlay_asset_id=long_overlay_asset_id,
    )

    _delete_unselected_cuts(analysis)

    to_finalize = list(
        AutoCutCorte.objects.filter(analysis=analysis, user_wants_finalize=True).select_related(
            "suggestion"
        )
    )
    total_to_finalize = len(to_finalize)
    finalization_failures: list[str] = []
    inventory_failures: list[str] = []

    use_gpu = has_nvenc()
    _queue = settings.CELERY_QUEUE_RENDER
    _workload = "gpu" if use_gpu else "cpu"
    _task_id = task.request.id or ""
    log_event(
        logger,
        event="render_started",
        queue_name=_queue,
        workload_type=_workload,
        task_id=_task_id,
        status="started",
        analysis_id=analysis_id,
        cuts_to_finalize=len(to_finalize),
    )
    _render_timer = Timer()

    for idx, corte in enumerate(to_finalize, start=1):
        analysis.progress_message = (
            f"Finalizando corte {idx}/{total_to_finalize}..."
            if total_to_finalize
            else "Finalizando cortes..."
        )
        analysis.progress = min(99, 95 + int(4 * idx / max(total_to_finalize, 1)))
        if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
            return

        cut_failure, inventory_failure = _process_cut(
            analysis, corte, opts, use_gpu=use_gpu, workload=_workload
        )
        if cut_failure:
            finalization_failures.append(cut_failure)
        if inventory_failure:
            inventory_failures.append(inventory_failure)

    # Inventory was synced above. Automatic scheduling runs ONLY at 19:00
    # via cron (generate_daily_factory_schedules_task). Not triggered here to avoid
    # scheduling new cuts outside the expected window.
    log_event(
        logger,
        event="render_finished",
        queue_name=_queue,
        workload_type=_workload,
        task_id=_task_id,
        duration_ms=_render_timer.elapsed_ms(),
        status="success" if not finalization_failures and not inventory_failures else "incomplete",
        analysis_id=analysis_id,
        cuts_finalized=len(to_finalize),
    )
    if finalization_failures or inventory_failures:
        analysis.status = "finalizing"
        analysis.progress_message = (
            "Finalização pendente de recovery."
            if finalization_failures
            else "Sincronização de inventário pendente de recovery."
        )
        analysis.error = (
            f"Finalização incompleta: {len(finalization_failures)} corte(s) com falha e "
            f"{len(inventory_failures)} sincronização(ões) com falha."
        )
        _safe_save_analysis(analysis, ["status", "progress_message", "error"])
        return

    _mark_analysis_done(analysis)


def _delete_unselected_cuts(analysis) -> None:
    """Apaga os cortes que o usuário desmarcou, com arquivo e sobra em disco.

    Parte destrutiva e irreversível da finalização. Dirigida **só** por
    `user_wants_finalize`.
    """
    to_delete = list(AutoCutCorte.objects.filter(analysis=analysis, user_wants_finalize=False))
    media_root = Path(settings.MEDIA_ROOT)
    cortes_dir = media_root / "auto_cuts" / "cortes"
    to_delete_sug_ids = {c.suggestion_id for c in to_delete}

    for corte in to_delete:
        if corte.file:
            try:
                fp = Path(corte.file.path) if corte.file.name else None
            except Exception:
                fp = None
            try:
                corte.file.delete(save=False)
            except Exception:
                pass
            if fp and fp.exists():
                try:
                    fp.unlink()
                except Exception:
                    pass
        corte.delete()

    if cortes_dir.exists() and to_delete_sug_ids:
        try:
            for sug_id in to_delete_sug_ids:
                for f in cortes_dir.glob(f"job_{analysis.id}_sug_{sug_id}.mp4"):
                    if f.exists():
                        f.unlink()
        except Exception:
            pass


def _process_cut(
    analysis,
    corte,
    opts: FinalizeOptions,
    *,
    use_gpu: bool,
    workload: str,
) -> tuple[str | None, str | None]:
    """Finaliza um corte. Devolve `(falha_de_corte, falha_de_inventário)`, ambas opcionais.

    Um corte que falha **perde** a marca de finalizado: sem isso, uma segunda passada o
    consideraria pronto sem nunca ter sido processado.
    """
    if not corte.file:
        if corte.is_finalized:
            corte.is_finalized = False
            corte.save(update_fields=["is_finalized"])
        logger.warning("Finalize skipped for cut %s: file field missing", corte.id)
        return f"cut:{corte.id}:missing_file_field", None

    video_path = Path(corte.file.path)
    if not video_path.exists():
        if corte.is_finalized:
            corte.is_finalized = False
            corte.save(update_fields=["is_finalized"])
        logger.warning("Finalize skipped for cut %s: file missing on disk", corte.id)
        return f"cut:{corte.id}:missing_file_on_disk", None

    cut_failure: str | None = None
    inventory_failure: str | None = None
    finalized_ok = False
    try:
        step_failed = _render_cut(analysis, corte, opts, video_path, use_gpu=use_gpu, workload=workload)
        finalized_ok = (
            not step_failed
            and bool(corte.file)
            and Path(corte.file.path).exists()
        )
    except Exception as e:
        cut_failure = f"cut:{corte.id}:exception:{type(e).__name__}"
        logger.exception("Finalize failed for cut %s: %s", corte.id, e)

    if finalized_ok:
        corte.is_finalized = True
        corte.save(update_fields=["is_finalized"])
        try:
            _sync_inventory_item_from_corte(corte)
        except Exception as e:
            inventory_failure = f"cut:{corte.id}:inventory:{type(e).__name__}"
            logger.exception("Inventory sync failed for cut %s: %s", corte.id, e)
    else:
        if cut_failure is None:
            cut_failure = f"cut:{corte.id}:incomplete"
        if corte.is_finalized:
            corte.is_finalized = False
            corte.save(update_fields=["is_finalized"])

    return cut_failure, inventory_failure


def _render_cut(
    analysis,
    corte,
    opts: FinalizeOptions,
    video_path: Path,
    *,
    use_gpu: bool,
    workload: str,
) -> bool:
    """O pipeline de vídeo de um corte, na ordem em que roda. Devolve `step_failed`.

    Cada etapa reescreve `corte.file` e devolve o novo caminho de trabalho. Uma etapa que
    falha não interrompe as seguintes — só impede o corte de contar como finalizado.
    """
    # Cut destination brand (target_brand override, distribute, or theme)
    target_brand = _resolve_target_brand_for_suggestion(analysis, corte.suggestion)
    brand_for_assets = target_brand or getattr(analysis, "brand", None)
    sug = corte.suggestion
    is_long_horizontal = (
        getattr(sug, "cut_type", "") == "long" and corte.format == "horizontal"
    )
    long_subs_ok = bool(getattr(brand_for_assets, "long_video_subtitles_enabled", False))
    long_logo_ok = bool(getattr(brand_for_assets, "long_video_logo_enabled", False))
    logo_path = _logo_path_for_brand(brand_for_assets)
    animation_path = _animation_path_for_brand(brand_for_assets, opts.overlay_animation_asset_id)
    # Long overlay: asset always from job brand (upload in Brands), not theme/distribute-routed brand.
    overlay_brand_for_long = getattr(analysis, "brand", None)
    long_overlay_path = (
        _long_overlay_path_for_brand(overlay_brand_for_long, opts.long_overlay_asset_id)
        if opts.long_overlay_enabled
        else None
    )

    work_path = video_path
    step_failed = False

    # 1. Reframe vertical (shorts with horizontal source)
    work_path, failed = _ensure_vertical_format(
        analysis, corte, opts, video_path, work_path, use_gpu=use_gpu, logo_path=logo_path
    )
    step_failed = step_failed or failed

    # 1b. Long 16:9: 1920×1080 canvas, SAR 1:1 and 30 fps before animation/overlay/logo/subs.
    # Otherwise anamorphic video or effective height < 1080 makes fixed-px logo and MarginV
    # look huge or misplaced (e.g. subtitle "in the middle").
    if is_long_horizontal and work_path.exists():
        work_path, failed = _ensure_long_canvas(analysis, corte, work_path, use_gpu=use_gpu)
        step_failed = step_failed or failed

    # 2. Overlay animation (short and long cuts, when requested)
    if animation_path and animation_path.exists() and work_path.exists():
        work_path, failed = _apply_overlay_animation(
            analysis, corte, opts, work_path, animation_path, use_gpu=use_gpu
        )
        step_failed = step_failed or failed

    # 2b. Right-side overlay (horizontal long cuts only)
    if (
        opts.long_overlay_enabled
        and long_overlay_path
        and long_overlay_path.exists()
        and is_long_horizontal
        and work_path.exists()
    ):
        work_path, failed = _apply_long_side_overlay(
            analysis, corte, work_path, long_overlay_path, use_gpu=use_gpu
        )
        step_failed = step_failed or failed

    # 3. Logo on horizontal long video (16:9), if brand has long_video_logo_enabled
    if (
        is_long_horizontal
        and long_logo_ok
        and logo_path
        and logo_path.exists()
        and work_path.exists()
    ):
        work_path, failed = _apply_long_logo(
            analysis, corte, opts, work_path, logo_path, use_gpu=use_gpu
        )
        step_failed = step_failed or failed

    # 4. Burn subtitles (shorts: if flagged; horizontal longs: only if brand allows)
    should_burn_subs = (
        corte.needs_subtitle
        and corte.subtitle_segments
        and (not is_long_horizontal or long_subs_ok)
    )
    if not corte.needs_subtitle:
        logger.info("Cut %s: skipping subtitles (needs_subtitle=False)", corte.id)
    elif not corte.subtitle_segments:
        logger.info("Cut %s: skipping subtitles (subtitle_segments empty)", corte.id)
    elif is_long_horizontal and not long_subs_ok:
        logger.info(
            "Cut %s: skipping subtitles (16:9 long: disabled in brand preferences)",
            corte.id,
        )
    if should_burn_subs and work_path.exists():
        failed = _burn_cut_subtitles(
            analysis,
            corte,
            opts,
            work_path,
            is_long_horizontal=is_long_horizontal,
            workload=workload,
        )
        step_failed = step_failed or failed

    return step_failed


def _ensure_vertical_format(
    analysis,
    corte,
    opts: FinalizeOptions,
    video_path: Path,
    work_path: Path,
    *,
    use_gpu: bool,
    logo_path,
) -> tuple[Path, bool]:
    """Corte vertical com fonte 16:9 é reenquadrado; fonte já vertical vira 1080×1920."""
    info = ffprobe_video_info(video_path)
    w, h = info.get("width", 0), info.get("height", 0)
    is_horizontal = w > 0 and h > 0 and w > h
    needs_reformat = (
        corte.format == "vertical"
        and is_horizontal
        and opts.vertical_mode in ("frame_center", "zoom_crop")
    )
    if needs_reformat:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                reformat_out = Path(tmpdir) / "reformatted.mp4"
                reformat_video_vertical(
                    video_path,
                    reformat_out,
                    opts.vertical_mode,
                    background_color=opts.background_color,
                    logo_path=logo_path,
                    title=(corte.suggestion.title or "").strip() if opts.vertical_mode == "frame_center" else "",
                    custom_text=opts.custom_text if opts.vertical_mode == "frame_center" else "",
                    font_size_title=opts.font_size_title,
                    font_size_text=opts.font_size_text,
                    title_color=opts.title_color,
                    text_color=opts.text_color,
                    use_gpu=use_gpu,
                )
                corte.file.delete(save=False)
                with open(reformat_out, "rb") as f:
                    corte.file.save(
                        f"job_{analysis.id}_sug_{corte.suggestion_id}_reformatted.mp4",
                        File(f),
                        save=True,
                    )
                work_path = Path(corte.file.path)
                logger.info("Cut %s: reframed to vertical (%s)", corte.id, opts.vertical_mode)
        except Exception as e:
            logger.exception("Vertical reframe failed for cut %s: %s", corte.id, e)
            return work_path, True
    elif corte.format == "vertical" and not is_horizontal:
        # Portrait/square/other aspect: force 1080×1920 (9:16) with pad (no crop)
        ar = (w / h) if h else 0.0
        target_ar = 9 / 16
        ok_ar = abs(ar - target_ar) < 0.02
        ok_px = w == 1080 and h == 1920
        if not (ok_ar and ok_px):
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    norm_out = Path(tmpdir) / "norm_vertical.mp4"
                    normalize_video_to_canvas(
                        work_path, norm_out, width=1080, height=1920, use_gpu=use_gpu
                    )
                    corte.file.delete(save=False)
                    with open(norm_out, "rb") as f:
                        corte.file.save(
                            f"job_{analysis.id}_sug_{corte.suggestion_id}_vert_norm.mp4",
                            File(f),
                            save=True,
                        )
                    work_path = Path(corte.file.path)
                    logger.info(
                        "Cut %s: normalized to 1080×1920 (9:16), source %dx%d",
                        corte.id, w, h,
                    )
            except Exception as e:
                logger.exception("9:16 vertical normalize failed for cut %s: %s", corte.id, e)
                return work_path, True
        else:
            logger.info(
                "Cut %s (vertical): already 1080×1920 9:16; no extra normalization",
                corte.id,
            )
    return work_path, False


def _ensure_long_canvas(analysis, corte, work_path: Path, *, use_gpu: bool) -> tuple[Path, bool]:
    """Longo 16:9 vai para 1920×1080, SAR 1:1 e 30 fps antes de qualquer overlay."""
    try:
        info_long = ffprobe_video_info(work_path)
        wl, hl = int(info_long.get("width", 0) or 0), int(info_long.get("height", 0) or 0)
        sar_f = ffprobe_sample_aspect_ratio_float(info_long.get("sample_aspect_ratio"))
        needs_canvas = wl != 1920 or hl != 1080
        if not needs_canvas and sar_f is not None and abs(sar_f - 1.0) > 0.03:
            needs_canvas = True
        if needs_canvas:
            with tempfile.TemporaryDirectory() as tmpdir:
                norm_long = Path(tmpdir) / "long_16x9_norm.mp4"
                normalize_video_to_canvas(
                    work_path,
                    norm_long,
                    width=1920,
                    height=1080,
                    use_gpu=use_gpu,
                    target_fps=30,
                    audio_hz=48000,
                )
                corte.file.delete(save=False)
                with open(norm_long, "rb") as f:
                    corte.file.save(
                        f"job_{analysis.id}_sug_{corte.suggestion_id}_long_norm.mp4",
                        File(f),
                        save=True,
                    )
                work_path = Path(corte.file.path)
                logger.info(
                    "Cut %s: normalized to 1920×1080 SAR 1:1 (horizontal long; was %d×%d sar=%s)",
                    corte.id,
                    wl,
                    hl,
                    info_long.get("sample_aspect_ratio") or "N/A",
                )
    except Exception as e:
        logger.exception(
            "Horizontal long normalize (16:9 canvas) failed for cut %s: %s",
            corte.id,
            e,
        )
        return work_path, True
    return work_path, False


def _apply_overlay_animation(
    analysis, corte, opts: FinalizeOptions, work_path: Path, animation_path: Path, *, use_gpu: bool
) -> tuple[Path, bool]:
    """Animação de marca sobre o vídeo (shorts e longos, quando pedida)."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            anim_out = Path(tmpdir) / "with_anim.mp4"
            overlay_animation(
                work_path,
                anim_out,
                animation_path,
                position=opts.overlay_position,
                margin=opts.overlay_margin,
                height=opts.overlay_height,
                use_gpu=use_gpu,
            )
            corte.file.delete(save=False)
            with open(anim_out, "rb") as f:
                corte.file.save(
                    f"job_{analysis.id}_sug_{corte.suggestion_id}_anim.mp4",
                    File(f),
                    save=True,
                )
            work_path = Path(corte.file.path)
            logger.info("Cut %s: overlay animation applied (%s)", corte.id, opts.overlay_position)
    except Exception as e:
        logger.exception("Overlay animation failed for cut %s: %s", corte.id, e)
        return work_path, True
    return work_path, False


def _apply_long_side_overlay(
    analysis, corte, work_path: Path, long_overlay_path: Path, *, use_gpu: bool
) -> tuple[Path, bool]:
    """Overlay lateral, só em longo horizontal."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            lo_out = Path(tmpdir) / "with_long_overlay.mp4"
            overlay_long_right(
                work_path,
                long_overlay_path,
                lo_out,
                use_gpu=use_gpu,
            )
            corte.file.delete(save=False)
            with open(lo_out, "rb") as f:
                corte.file.save(
                    f"job_{analysis.id}_sug_{corte.suggestion_id}_long_overlay.mp4",
                    File(f),
                    save=True,
                )
            work_path = Path(corte.file.path)
            logger.info("Cut %s: side (long) overlay applied", corte.id)
    except Exception as e:
        logger.exception(
            "Long side overlay failed for cut %s: %s", corte.id, e
        )
        return work_path, True
    return work_path, False


def _apply_long_logo(
    analysis, corte, opts: FinalizeOptions, work_path: Path, logo_path: Path, *, use_gpu: bool
) -> tuple[Path, bool]:
    """Marca d'água no longo 16:9, quando a brand pede."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            logo_out = Path(tmpdir) / "with_logo.mp4"
            overlay_logo(
                work_path,
                logo_out,
                logo_path,
                x=opts.logo_x,
                y=opts.logo_y,
                logo_height=160,
                opacity=0.8,
                use_gpu=use_gpu,
            )
            corte.file.delete(save=False)
            with open(logo_out, "rb") as f:
                corte.file.save(
                    f"job_{analysis.id}_sug_{corte.suggestion_id}_logo.mp4",
                    File(f),
                    save=True,
                )
            work_path = Path(corte.file.path)
            logger.info("Cut %s: logo inserted at (%d,%d)", corte.id, opts.logo_x, opts.logo_y)
    except Exception as e:
        logger.exception("Logo insert failed for cut %s: %s", corte.id, e)
        return work_path, True
    return work_path, False


def _burn_cut_subtitles(
    analysis,
    corte,
    opts: FinalizeOptions,
    work_path: Path,
    *,
    is_long_horizontal: bool,
    workload: str,
) -> bool:
    """Queima a legenda no vídeo. Devolve `step_failed`.

    É a única etapa com métrica própria de render — é a mais cara das seis.
    """
    try:
        render_jobs_total.labels(workload_type=workload).inc()
        _burn_timer = Timer()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            srt_path = tmppath / "subtitles.srt"
            srt_path.write_text(
                segments_to_srt(corte.subtitle_segments), encoding="utf-8"
            )
            output_tmp = tmppath / "output_with_subs.mp4"
            base_style = (
                DEFAULT_SUBTITLE_STYLE_LONG
                if is_long_horizontal
                else DEFAULT_SUBTITLE_STYLE_SHORT
            )
            style = {**base_style, **opts.subtitle_style}
            # Shorts: subtitles at bottom (above YouTube buttons), not top
            # MarginV = distance from bottom edge. 160px keeps ~20px above button area.
            style["position"] = "bottom"
            style["margin_v"] = style.get("margin_v", 160)
            burn_subtitles(
                work_path,
                srt_path,
                output_tmp,
                style,
                segments=corte.subtitle_segments,
            )
            corte.file.delete(save=False)
            with open(output_tmp, "rb") as f:
                corte.file.save(
                    f"job_{analysis.id}_sug_{corte.suggestion_id}_final.mp4",
                    File(f),
                    save=True,
                )
            logger.info("Cut %s: subtitles burned", corte.id)
        render_duration_ms.labels(workload_type=workload).observe(
            _burn_timer.elapsed_ms()
        )
    except Exception as e:
        render_failures_total.labels(workload_type=workload).inc()
        logger.exception("Subtitle burn failed for cut %s: %s", corte.id, e)
        return True
    return False
