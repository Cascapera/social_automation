"""Celery tasks for automatic cut analysis."""

import logging
import tempfile
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File

from apps.auto_cuts.services.analysis_flow import run_analysis
from apps.auto_cuts.services.flow_common import (
    _mark_analysis_done,
    _resolve_target_brand_for_suggestion,
    _safe_save_analysis,
    _sanitize_long_overlay_fk,
    _sync_inventory_item_from_corte,
)
from apps.common.metrics import (
    render_duration_ms,
    render_failures_total,
    render_jobs_total,
)
from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.services.ffmpeg import (
    has_nvenc,
    normalize_video_to_canvas,
)
from apps.jobs.services.subtitles import burn_subtitles, segments_to_srt

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def analyze_auto_cuts_task(self, analysis_id: int) -> None:
    """Transcribe, analyze in chunks, and aggregate viral cut suggestions.

    O corpo mora em services/analysis_flow.py (R-19). A task fica aqui porque o nome
    dela e contrato de fila: `apps.auto_cuts.tasks.analyze_auto_cuts_task` esta em
    settings.CELERY_TASK_ROUTES e em mensagens que podem estar em voo.
    """
    run_analysis(self, analysis_id)


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


@shared_task(bind=True)
def finalizar_auto_cut_task(
    self,
    analysis_id: int,
    subtitle_style: dict | None = None,
    vertical_mode: str | None = None,
    background_color: str | None = None,
    custom_text: str | None = None,
    font_size_title: int | None = None,
    font_size_text: int | None = None,
    title_color: str | None = None,
    text_color: str | None = None,
    horizontal_insert_logo: bool = False,
    horizontal_logo_x: int | None = None,
    horizontal_logo_y: int | None = None,
    overlay_animation_asset_id: int | None = None,
    overlay_position: str | None = None,
    overlay_margin: int | None = None,
    overlay_height: int | None = None,
    long_overlay_enabled: bool | None = None,
    long_overlay_asset_id: int | None = None,
) -> None:
    """
    Finalize cuts: delete unselected, reframe verticals (if 16:9 source),
    burn subtitles on cuts with needs_subtitle, mark all as finalized.
    """
    from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte
    from apps.auto_cuts.services.vertical_reformat import reformat_video_vertical
    from apps.brands.models import BrandAsset
    from apps.jobs.services.ffmpeg import (
        ffprobe_sample_aspect_ratio_float,
        ffprobe_video_info,
        overlay_animation,
        overlay_logo,
        overlay_long_right,
    )

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
    analysis.progress = min(99, max(int(getattr(analysis, "progress", 0) or 0), 95))
    analysis.error = ""
    if not _safe_save_analysis(analysis, ["status", "progress_message", "progress", "error"]):
        return

    user_subtitle_style = subtitle_style or {}
    vert_mode = vertical_mode or "zoom_crop"
    bg_color = (background_color or "#000000").strip()
    link_text = (custom_text or "").strip()
    title_font = 36 if font_size_title is None else max(12, min(96, int(font_size_title)))
    text_font = 28 if font_size_text is None else max(12, min(72, int(font_size_text)))
    title_clr = (title_color or "#FFFFFF").strip()
    text_clr = (text_color or "#FFFFFF").strip()
    # Logo as watermark: top-left, 40px margin, 80% opacity
    horiz_logo_x = max(0, min(2000, int(horizontal_logo_x or 40)))
    horiz_logo_y = max(0, min(1200, int(horizontal_logo_y or 40)))
    overlay_pos = (overlay_position or "bottom_right").strip() or "bottom_right"
    overlay_m = max(0, min(100, int(overlay_margin or 24)))
    overlay_h = max(20, min(400, int(overlay_height or 120)))

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

    if long_overlay_enabled is None:
        lo_enabled = bool(getattr(analysis, "long_overlay_enabled", False))
    else:
        lo_enabled = bool(long_overlay_enabled)
    if long_overlay_asset_id is None:
        lo_asset_id = getattr(analysis, "long_overlay_asset_id", None)
    else:
        lo_asset_id = int(long_overlay_asset_id) if long_overlay_asset_id else None

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
    _task_id = self.request.id or ""
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

        finalized_ok = False
        failure_recorded = False
        if not corte.file:
            finalization_failures.append(f"cut:{corte.id}:missing_file_field")
            failure_recorded = True
            if corte.is_finalized:
                corte.is_finalized = False
                corte.save(update_fields=["is_finalized"])
            logger.warning("Finalize skipped for cut %s: file field missing", corte.id)
            continue

        video_path = Path(corte.file.path)
        if not video_path.exists():
            finalization_failures.append(f"cut:{corte.id}:missing_file_on_disk")
            failure_recorded = True
            if corte.is_finalized:
                corte.is_finalized = False
                corte.save(update_fields=["is_finalized"])
            logger.warning("Finalize skipped for cut %s: file missing on disk", corte.id)
            continue

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
        animation_path = _animation_path_for_brand(brand_for_assets, overlay_animation_asset_id)
        # Long overlay: asset always from job brand (upload in Brands), not theme/distribute-routed brand.
        overlay_brand_for_long = getattr(analysis, "brand", None)
        long_overlay_path = (
            _long_overlay_path_for_brand(overlay_brand_for_long, lo_asset_id) if lo_enabled else None
        )

        try:
            work_path = video_path
            step_failed = False
            # 1. Reframe vertical (shorts with horizontal source)
            info = ffprobe_video_info(video_path)
            w, h = info.get("width", 0), info.get("height", 0)
            is_horizontal = w > 0 and h > 0 and w > h
            needs_reformat = (
                corte.format == "vertical"
                and is_horizontal
                and vert_mode in ("frame_center", "zoom_crop")
            )
            if needs_reformat:
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        reformat_out = Path(tmpdir) / "reformatted.mp4"
                        reformat_video_vertical(
                            video_path,
                            reformat_out,
                            vert_mode,
                            background_color=bg_color,
                            logo_path=logo_path,
                            title=(corte.suggestion.title or "").strip() if vert_mode == "frame_center" else "",
                            custom_text=link_text if vert_mode == "frame_center" else "",
                            font_size_title=title_font,
                            font_size_text=text_font,
                            title_color=title_clr,
                            text_color=text_clr,
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
                        logger.info("Cut %s: reframed to vertical (%s)", corte.id, vert_mode)
                except Exception as e:
                    step_failed = True
                    logger.exception("Vertical reframe failed for cut %s: %s", corte.id, e)
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
                        step_failed = True
                        logger.exception("9:16 vertical normalize failed for cut %s: %s", corte.id, e)
                else:
                    logger.info(
                        "Cut %s (vertical): already 1080×1920 9:16; no extra normalization",
                        corte.id,
                    )

            # 1b. Long 16:9: 1920×1080 canvas, SAR 1:1 and 30 fps before animation/overlay/logo/subs.
            # Otherwise anamorphic video or effective height < 1080 makes fixed-px logo and MarginV
            # look huge or misplaced (e.g. subtitle “in the middle”).
            if is_long_horizontal and work_path.exists():
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
                    step_failed = True
                    logger.exception(
                        "Horizontal long normalize (16:9 canvas) failed for cut %s: %s",
                        corte.id,
                        e,
                    )

            # 2. Overlay animation (short and long cuts, when requested)
            if animation_path and animation_path.exists() and work_path.exists():
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        anim_out = Path(tmpdir) / "with_anim.mp4"
                        overlay_animation(
                            work_path,
                            anim_out,
                            animation_path,
                            position=overlay_pos,
                            margin=overlay_m,
                            height=overlay_h,
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
                        logger.info("Cut %s: overlay animation applied (%s)", corte.id, overlay_pos)
                except Exception as e:
                    step_failed = True
                    logger.exception("Overlay animation failed for cut %s: %s", corte.id, e)

            # 2b. Right-side overlay (horizontal long cuts only)
            if (
                lo_enabled
                and long_overlay_path
                and long_overlay_path.exists()
                and is_long_horizontal
                and work_path.exists()
            ):
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
                    step_failed = True
                    logger.exception(
                        "Long side overlay failed for cut %s: %s", corte.id, e
                    )

            # 3. Logo on horizontal long video (16:9), if brand has long_video_logo_enabled
            if (
                is_long_horizontal
                and long_logo_ok
                and logo_path
                and logo_path.exists()
                and work_path.exists()
            ):
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        logo_out = Path(tmpdir) / "with_logo.mp4"
                        overlay_logo(
                            work_path,
                            logo_out,
                            logo_path,
                            x=horiz_logo_x,
                            y=horiz_logo_y,
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
                        logger.info("Cut %s: logo inserted at (%d,%d)", corte.id, horiz_logo_x, horiz_logo_y)
                except Exception as e:
                    step_failed = True
                    logger.exception("Logo insert failed for cut %s: %s", corte.id, e)

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
            if should_burn_subs:
                if work_path.exists():
                    try:
                        render_jobs_total.labels(workload_type=_workload).inc()
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
                            style = {**base_style, **user_subtitle_style}
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
                        render_duration_ms.labels(workload_type=_workload).observe(
                            _burn_timer.elapsed_ms()
                        )
                    except Exception as e:
                        step_failed = True
                        render_failures_total.labels(workload_type=_workload).inc()
                        logger.exception("Subtitle burn failed for cut %s: %s", corte.id, e)
            finalized_ok = (
                not step_failed
                and bool(corte.file)
                and Path(corte.file.path).exists()
            )
        except Exception as e:
            finalization_failures.append(f"cut:{corte.id}:exception:{type(e).__name__}")
            failure_recorded = True
            logger.exception("Finalize failed for cut %s: %s", corte.id, e)
        if finalized_ok:
            corte.is_finalized = True
            corte.save(update_fields=["is_finalized"])
            try:
                _sync_inventory_item_from_corte(corte)
            except Exception as e:
                inventory_failures.append(f"cut:{corte.id}:inventory:{type(e).__name__}")
                logger.exception("Inventory sync failed for cut %s: %s", corte.id, e)
        else:
            if not failure_recorded:
                finalization_failures.append(f"cut:{corte.id}:incomplete")
            if corte.is_finalized:
                corte.is_finalized = False
                corte.save(update_fields=["is_finalized"])

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
