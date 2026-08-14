"""Celery tasks for automatic cut analysis."""

import logging
import os
import tempfile
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File
from django.db.utils import DatabaseError
from django.utils import timezone

from apps.auto_cuts.services.analysis_flow import (
    _process_ready_cuts_batch_flow,
    _process_ready_cuts_flow,
)
from apps.auto_cuts.services.flow_common import (
    _append_convidados,
    _mark_analysis_done,
    _queue_analysis_finalization,
    _resolve_target_brand_for_suggestion,
    _safe_save_analysis,
    _sanitize_long_overlay_fk,
    _sync_inventory_item_from_corte,
)
from apps.auto_cuts.services.grok import (
    analyze_chunks_in_one_request,
)
from apps.auto_cuts.services.transcript import (
    chunk_transcript,
    segments_to_transcript_with_timestamps,
)
from apps.auto_cuts.services.video_chunks import (
    cleanup_cortes_processo,
    extract_chunks_to_folder,
    transcribe_single_chunk,
)
from apps.common.metrics import (
    render_duration_ms,
    render_failures_total,
    render_jobs_total,
    transcription_duration_ms,
    transcription_failures_total,
    transcription_jobs_total,
)
from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.services.ffmpeg import (
    ffprobe_duration,
    has_nvenc,
    normalize_video_to_canvas,
)
from apps.jobs.services.subtitles import burn_subtitles, generate_subtitles, segments_to_srt

logger = logging.getLogger(__name__)


# Videos longer than this use chunked transcription (avoids OOM)
CHUNKED_TRANSCRIPTION_THRESHOLD_SEC = 10 * 60  # 10 min
VIRAL_SHORT_MIN_SEC = 30
VIRAL_SHORT_MAX_SEC = 60
# viral_long / viral_long_en: target 80–160s; shorter cuts may be kept if score > 95
VIRAL_LONG_SHORT_MIN_SEC = 80
VIRAL_LONG_SHORT_MAX_SEC = 160
VIRAL_LONG_SHORT_SCORE_KEEP_IF_SHORT = 95  # keep even below min duration if score clears bar
VIRAL_LONG_MIN_SEC = 8 * 60
VIRAL_LONG_MAX_SEC = 15 * 60
EDUCATIONAL_SHORT_MAX_SEC = 180
THEME_CATEGORY_NORMALIZATION = {
    "business_money": "BUSINESS_MONEY",
    "business": "BUSINESS_MONEY",
    "money": "BUSINESS_MONEY",
    "negocios_dinheiro": "BUSINESS_MONEY",
    "negocios": "BUSINESS_MONEY",
    "dinheiro": "BUSINESS_MONEY",
    "psychology_relationships": "PSYCHOLOGY_RELATIONSHIPS",
    "psychology": "PSYCHOLOGY_RELATIONSHIPS",
    "relationships": "PSYCHOLOGY_RELATIONSHIPS",
    "psicologia_relacionamentos": "PSYCHOLOGY_RELATIONSHIPS",
    "psicologia": "PSYCHOLOGY_RELATIONSHIPS",
    "relacionamentos": "PSYCHOLOGY_RELATIONSHIPS",
    "stories_curiosities": "STORIES_CURIOSITIES",
    "stories": "STORIES_CURIOSITIES",
    "curiosities": "STORIES_CURIOSITIES",
    "historias_curiosidades": "STORIES_CURIOSITIES",
    "historias": "STORIES_CURIOSITIES",
    "curiosidades": "STORIES_CURIOSITIES",
    "controversies_debate": "CONTROVERSIES_DEBATE",
    "controversies": "CONTROVERSIES_DEBATE",
    "debate": "CONTROVERSIES_DEBATE",
    "polemicas_debate": "CONTROVERSIES_DEBATE",
    "polemicas": "CONTROVERSIES_DEBATE",
    "comedy_humor": "COMEDY_HUMOR",
    "comedy": "COMEDY_HUMOR",
    "humor": "COMEDY_HUMOR",
}
ALL_THEME_CATEGORIES = [
    "BUSINESS_MONEY",
    "PSYCHOLOGY_RELATIONSHIPS",
    "STORIES_CURIOSITIES",
    "CONTROVERSIES_DEBATE",
    "COMEDY_HUMOR",
]


def _pick_timestamp(item: dict, start: bool = True) -> str:
    """Accept legacy and new timestamp keys."""
    if start:
        return item.get("start") or item.get("start_timestamp") or ""
    return item.get("end") or item.get("end_timestamp") or ""


def _normalize_virality_score(value) -> int | None:
    """Convert score to int 0..100 (accepts '96%' or number)."""
    if value is None:
        return None
    try:
        score = int(float(str(value).replace("%", "").strip()))
    except Exception:
        return None
    return max(0, min(100, score))


def _sort_by_virality(items: list[dict]) -> list[dict]:
    """Sort items by viral score desc, with rank asc as tiebreaker."""
    return sorted(
        items,
        key=lambda item: (
            -(_normalize_virality_score(item.get("virality_score")) or -1),
            int(item.get("rank") or 9999),
        ),
    )


def _estimate_short_duration_seconds(item: dict, tc_to_seconds) -> float:
    """Duration in seconds from duration_seconds in JSON or timecodes."""
    ds = item.get("duration") or item.get("duration_seconds")
    if ds is not None:
        try:
            d = float(ds)
            if d > 0:
                return d
        except (TypeError, ValueError):
            pass
    st = _pick_timestamp(item, True)
    en = _pick_timestamp(item, False)
    try:
        return max(0.0, float(tc_to_seconds(en) - tc_to_seconds(st)))
    except Exception:
        return 0.0


def _sort_shorts_viral_long(items: list[dict], tc_to_seconds) -> list[dict]:
    """
    Sort viral_long shorts: combine viral score and duration (up to 160s).
    50% virality_score + 50% normalized duration — favors longer clips with good score.
    """
    def composite(item: dict) -> float:
        score = float(_normalize_virality_score(item.get("virality_score")) or 0)
        dur = _estimate_short_duration_seconds(item, tc_to_seconds)
        dur = max(0.0, min(dur, float(VIRAL_LONG_SHORT_MAX_SEC)))
        dur_part = (dur / float(VIRAL_LONG_SHORT_MAX_SEC)) * 100.0
        return 0.5 * score + 0.5 * dur_part

    return sorted(
        items,
        key=lambda item: (-composite(item), -(_normalize_virality_score(item.get("virality_score")) or -1)),
    )


def _normalize_theme_category(value: str, fallback: str = "") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    if raw in (
        "BUSINESS_MONEY",
        "PSYCHOLOGY_RELATIONSHIPS",
        "STORIES_CURIOSITIES",
        "CONTROVERSIES_DEBATE",
        "COMEDY_HUMOR",
    ):
        return raw
    key = (
        raw.lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
    )
    key = "_".join([p for p in key.split("_") if p])
    return THEME_CATEGORY_NORMALIZATION.get(key, fallback)


def _filter_factory_routable_items(analysis, items: list[dict]) -> tuple[list[dict], int, int]:
    """
    In factory context:
    - drop items without valid category;
    - drop items whose category has no mapped brand;
    - returns (valid_items, missing_category_count, unmapped_count).
    When target_brand is set, pass all items through without filtering.
    """
    if getattr(analysis, "target_brand_id", None):
        return list(items or []), 0, 0
    if (getattr(analysis, "distribution_mode", "") or "").strip() == "distribute":
        return list(items or []), 0, 0
    base_brand = getattr(analysis, "brand", None)
    factory_id = getattr(base_brand, "factory_id", None) if base_brand else None
    if not factory_id:
        return list(items or []), 0, 0

    from apps.brands.models import Brand, BrandCategory

    active_codes = set(
        BrandCategory.objects.filter(factory_id=factory_id, is_active=True)
        .values_list("code", flat=True)
    )
    brand_codes = {
        b.theme_category
        for b in Brand.objects.filter(factory_id=factory_id).exclude(theme_category="")
    }
    category_set = brand_codes & active_codes
    missing_category = 0
    unmapped_count = 0
    valid_items: list[dict] = []

    for item in (items or []):
        normalized = _normalize_theme_category(item.get("theme_category"), fallback="")
        if not normalized:
            missing_category += 1
            continue
        if normalized not in category_set:
            unmapped_count += 1
            continue
        item_copy = dict(item)
        item_copy["theme_category"] = normalized
        valid_items.append(item_copy)

    return valid_items, missing_category, unmapped_count


def _allowed_theme_categories_for_analysis(analysis) -> list[str]:
    """
    In factory context, return only codes que estão vinculados a uma brand da factory
    E que pertencem a uma BrandCategory ativa. Fora de factory, retorna o fallback default.
    """
    base_brand = getattr(analysis, "brand", None)
    factory_id = getattr(base_brand, "factory_id", None) if base_brand else None
    if not factory_id:
        return list(ALL_THEME_CATEGORIES)
    from apps.brands.models import Brand, BrandCategory

    active_codes = set(
        BrandCategory.objects.filter(factory_id=factory_id, is_active=True)
        .values_list("code", flat=True)
    )
    brand_codes = {
        str(b.theme_category or "").strip()
        for b in Brand.objects.filter(factory_id=factory_id).exclude(theme_category="")
        if str(b.theme_category or "").strip()
    }
    mapped = sorted(brand_codes & active_codes)
    return mapped or list(ALL_THEME_CATEGORIES)


def _is_factory_processing_paused(analysis) -> bool:
    brand = getattr(analysis, "brand", None)
    if not brand:
        return False
    factory = getattr(brand, "factory", None)
    return bool(factory and getattr(factory, "processing_paused", False))


def _is_brand_only(analysis) -> bool:
    """True when target_brand is set or brand has no factory (brand-only content)."""
    if getattr(analysis, "target_brand_id", None):
        return True
    brand = getattr(analysis, "brand", None)
    if not brand:
        return False
    return getattr(brand, "factory_id", None) is None


def _was_transcript_prepopulated_by_multi_creator(analysis) -> bool:
    """True quando a analysis foi criada pelo Multiple-Creator e ja recebeu
    transcript_segments pre-populados do MultipleCreatorJob pai.

    Quando True, analyze_auto_cuts_task pula download do YouTube + transcricao
    e cai direto na fase de analise LLM.
    """
    if not getattr(analysis, "transcript_segments", None):
        return False
    try:
        from apps.multiple_creator.models import MultipleCreatorBrandExecution
    except ImportError:
        return False
    return MultipleCreatorBrandExecution.objects.filter(
        auto_cut_analysis_id=analysis.id
    ).exists()


@shared_task(bind=True)
def analyze_auto_cuts_task(self, analysis_id: int) -> None:
    """Transcribe, analyze in chunks, and aggregate viral cut suggestions."""
    from apps.auto_cuts.models import AutoCutAnalysis, AutoCutSuggestion

    try:
        analysis = AutoCutAnalysis.objects.get(id=analysis_id)
    except ObjectDoesNotExist:
        return  # Analysis deleted; ignore queued task

    if _sanitize_long_overlay_fk(analysis):
        analysis.save(update_fields=["long_overlay_asset_id", "long_overlay_enabled"])
        logger.warning(
            "[FLUXO] Analysis %s: orphan side overlay (long_overlay_asset); option disabled.",
            analysis_id,
        )

    # Idempotency: if the full pipeline already finished, or if post-processing
    # is already in progress, do not restart the analysis stage.
    if analysis.status in ("done", "finalizing"):
        logger.debug(
            "[FLUXO] Analysis %s already advanced to %s; skip duplicate analyze delivery.",
            analysis_id,
            analysis.status,
        )
        return

    # Cooperative queue pause: does not interrupt a running job, only prevents
    # starting new jobs while the factory is paused.
    if _is_factory_processing_paused(analysis):
        analysis.status = "pending"
        analysis.progress_message = "Fila de jobs pausada para esta factory. Aguardando retomada..."
        analysis.progress = 0
        analysis.error = ""
        if _safe_save_analysis(analysis, ["status", "progress_message", "progress", "error"]):
            self.apply_async(args=[analysis_id], countdown=60)
        logger.info("[FLUXO] Analysis %s deferred: factory has processing_paused.", analysis_id)
        return

    # Ready cuts: batch (multiple files → one job)
    if getattr(analysis, "is_ready_cuts", False):
        from apps.auto_cuts.models import AutoCutReadyChunk

        if AutoCutReadyChunk.objects.filter(analysis_id=analysis_id).exists():
            try:
                _process_ready_cuts_batch_flow(analysis_id)
            except Exception as e:
                logger.exception("[FLUXO] Ready cuts batch error: %s", e)
                AutoCutAnalysis.objects.filter(id=analysis_id).update(
                    status="error",
                    error=str(e),
                    updated_at=timezone.now(),
                )
            return

    multi_creator_skip = _was_transcript_prepopulated_by_multi_creator(analysis)

    youtube_url = (analysis.youtube_url or "").strip()
    pv = (analysis.prompt_version or "viral").strip().lower()
    transcript_lang = "en" if pv in ("viral_en", "viral_long_en", "educational_en", "viral_translate") else "pt"
    if not multi_creator_skip:
        analysis.status = "transcribing"
        analysis.progress_message = "Baixando vídeo do YouTube..." if youtube_url else "Transcrevendo vídeo..."
        analysis.progress = 2 if youtube_url else 5
        analysis.error = ""
        analysis.save(update_fields=["status", "progress_message", "progress", "error"])

    # If YouTube URL, download first (skip quando o job pai Multi-Creator ja baixou)
    if youtube_url and not multi_creator_skip:
        analysis.progress_message = "Baixando vídeo do YouTube..."
        analysis.progress = 2
        if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
            return
        try:
            from apps.auto_cuts.services.youtube_download import download_youtube
            media_root = Path(settings.MEDIA_ROOT)
            download_dir = media_root / "auto_cuts" / "sources"
            download_dir.mkdir(parents=True, exist_ok=True)
            out_path = download_dir / f"yt_{analysis_id}.mp4"
            downloaded = download_youtube(youtube_url, out_path)
            with open(downloaded, "rb") as f:
                analysis.file.save(downloaded.name, File(f), save=True)
            # Keep youtube_url for publish metadata (full description/episode).
            analysis.save(update_fields=["file"])
            # Remove original yt-dlp file if elsewhere (e.g. video_id.mp4)
            saved_path = Path(analysis.file.path)
            if downloaded.resolve() != saved_path.resolve() and downloaded.exists():
                try:
                    downloaded.unlink()
                except Exception:
                    pass
            logger.info("[FLUXO] YouTube downloaded: %s", analysis.file.name)
        except Exception as e:
            logger.exception("[FLUXO] YouTube download failed: %s", e)
            analysis.status = "error"
            analysis.error = f"Erro ao baixar vídeo: {e}"
            analysis.save(update_fields=["status", "error"])
            return

    # Resolve video path
    video_file = analysis.video_file
    if not video_file:
        analysis.status = "error"
        analysis.error = "Nenhum vídeo encontrado (source ou upload)."
        analysis.save(update_fields=["status", "error"])
        return

    video_path = Path(video_file.path)
    if not video_path.exists():
        analysis.status = "error"
        analysis.error = "Arquivo de vídeo não existe no disco."
        analysis.save(update_fields=["status", "error"])
        return

    _t_queue = settings.CELERY_QUEUE_TRANSCRIPTION
    _t_workload = "cpu" if getattr(settings, "WHISPER_FORCE_CPU", True) else "gpu"
    _t_task_id = self.request.id or ""
    if not multi_creator_skip:
        log_event(
            logger,
            event="transcription_started",
            queue_name=_t_queue,
            workload_type=_t_workload,
            task_id=_t_task_id,
            status="started",
            source_video_id=analysis_id,
        )
        transcription_jobs_total.labels(workload_type=_t_workload).inc()
    _t_timer = Timer()

    try:
        if multi_creator_skip:
            log_event(
                logger,
                event="multiple_creator_transcription_skipped",
                queue_name=_t_queue,
                workload_type=_t_workload,
                task_id=_t_task_id,
                status="skipped",
                source_video_id=analysis_id,
            )
            duration_sec = ffprobe_duration(video_path)
            use_chunked = False
        else:
            duration_sec = ffprobe_duration(video_path)
            use_chunked = duration_sec > CHUNKED_TRANSCRIPTION_THRESHOLD_SEC

        if multi_creator_skip:
            pass  # transcript_segments ja vieram populados do MultipleCreatorJob pai
        elif use_chunked:
            # Flow: extract chunks → save under cortes_processo → transcribe one by one → delete chunk
            # Each chunk = 18 min (small files on disk, no huge temp in memory)
            try:
                analysis.progress_message = "Extraindo blocos de áudio..."
                if not _safe_save_analysis(analysis, ["progress_message"]):
                    transcription_failures_total.labels(workload_type=_t_workload).inc()
                    return
                chunk_paths = extract_chunks_to_folder(
                    video_path, analysis.id,
                    chunk_minutes=18, overlap_minutes=3)
                total_chunks = len(chunk_paths)
                all_segments = []
                boundaries = [(s, e) for _, s, e in chunk_paths]

                # Simple loop (no generator) — avoids crash when exiting generator on long videos
                from apps.jobs.services.subtitles import load_whisper_model
                _whisper_model, _ = load_whisper_model(model_size=os.getenv("WHISPER_MODEL", "small").strip() or "small", device=None)

                for i, (chunk_path, start_sec, end_sec) in enumerate(chunk_paths):
                    analysis.progress_message = f"Transcrevendo bloco {i + 1}/{total_chunks}..."
                    analysis.progress = 5 + int(15 * (i + 1) / total_chunks)
                    if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
                        logger.info("[FLUXO] Analysis %s deleted during transcription; aborting.", analysis_id)
                        transcription_failures_total.labels(workload_type=_t_workload).inc()
                        return
                    chunk = transcribe_single_chunk(_whisper_model, chunk_path, start_sec, end_sec, language=transcript_lang)
                    prev_end = boundaries[i - 1][1] if i > 0 else 0
                    segs_to_add = [
                        {"start": s.get("start"), "end": s.get("end"), "text": s.get("text", "").strip()}
                        for s in chunk["segments"]
                        if s.get("start", 0) >= prev_end
                    ]
                    all_segments.extend(segs_to_add)
                    chunk["segments"] = []  # free memory
                    logger.info("[FLUXO] Chunk %d/%d: OK (total %d segments)", i + 1, total_chunks, len(all_segments))

                # Do not del/gc here — explicit GPU release can crash on Windows
                logger.info("[FLUXO] Transcription loop OK. %d segments. Sorting...", len(all_segments))
                all_segments.sort(key=lambda s: s.get("start", 0))
                logger.info("[FLUXO] Sorted. Building transcript string...")
                analysis.transcript_segments = all_segments
                analysis.transcript = segments_to_transcript_with_timestamps(all_segments)
                logger.info("[FLUXO] Transcript built (%d chars).", len(analysis.transcript or ""))
            finally:
                logger.info("[FLUXO] Starting cortes_processo cleanup...")
                cleanup_cortes_processo(analysis.id)
                logger.info("[FLUXO] Chunked transcription done. Cleanup done.")
        else:
            # Original flow: transcribe whole video
            segments = generate_subtitles(video_path, language=transcript_lang)
            if not segments:
                analysis.status = "error"
                analysis.error = "Nenhum segmento transcrito."
                analysis.save(update_fields=["status", "error"])
                transcription_failures_total.labels(workload_type=_t_workload).inc()
                return

            analysis.transcript_segments = segments
            analysis.transcript = segments_to_transcript_with_timestamps(segments)
            logger.info("[FLUXO] Single-pass transcription done.")

        segments = analysis.transcript_segments or []
        if not segments:
            analysis.status = "error"
            analysis.error = "Transcrição vazia."
            analysis.save(update_fields=["status", "error"])
            transcription_failures_total.labels(workload_type=_t_workload).inc()
            return

        if not multi_creator_skip:
            transcription_duration_ms.labels(workload_type=_t_workload).observe(_t_timer.elapsed_ms())
            log_event(
                logger,
                event="transcription_finished",
                queue_name=_t_queue,
                workload_type=_t_workload,
                task_id=_t_task_id,
                duration_ms=_t_timer.elapsed_ms(),
                status="success",
                source_video_id=analysis_id,
                segments_count=len(segments),
            )

        # "Ready cuts" flow: video already edited, only needs metadata (title, thumbnail)
        if getattr(analysis, "is_ready_cuts", False):
            _process_ready_cuts_flow(analysis, duration_sec, segments)
            return

        logger.info("[FLUXO] Starting chunk_transcript (%d segments)...", len(segments))
        chunks = chunk_transcript(segments, chunk_minutes=18, overlap_minutes=3)
        logger.info("[FLUXO] chunk_transcript done: %d blocks.", len(chunks) if chunks else 0)
        if not chunks:
            analysis.status = "error"
            analysis.error = "Não foi possível dividir a transcrição em blocos."
            analysis.save(update_fields=["status", "error"])
            return

        analysis.status = "analyzing"
        analysis.progress_message = f"Analisando {len(chunks)} blocos com IA (1 requisição)..."
        analysis.progress = 20
        analysis.save(update_fields=["transcript_segments", "transcript", "status", "progress_message", "progress"])

        logger.info("[FLUXO] Calling Grok API (analyze_chunks_in_one_request)... %d blocks", len(chunks))
        MAX_RETRIES = 3
        brand_only = _is_brand_only(analysis)
        allowed_theme_categories = _allowed_theme_categories_for_analysis(analysis)
        if getattr(analysis, "target_brand_id", None):
            target_brand_obj = getattr(analysis, "target_brand", None)
            factory = getattr(target_brand_obj, "factory", None) if target_brand_obj else None
            factory_name = getattr(factory, "name", None) or "?"
            brand_name = getattr(target_brand_obj, "name", None) or f"Brand #{analysis.target_brand_id}"
            logger.info(
                "[FLUXO] Factory (%s) : %s : LLM theme_category ignored (brand-only content).",
                factory_name,
                brand_name,
            )
        elif brand_only:
            base_brand = getattr(analysis, "brand", None)
            factory = getattr(base_brand, "factory", None) if base_brand else None
            if factory:
                factory_name = getattr(factory, "name", None) or "?"
                logger.info(
                    "[FLUXO] Factory (%s) : all : LLM theme_category ignored (brand-only content).",
                    factory_name,
                )
            else:
                logger.info("[FLUXO] Brand without factory: LLM theme_category ignored (brand-only content).")
        else:
            logger.info(
                "[FLUXO] Allowed categories for routing in this job: %s",
                ", ".join(allowed_theme_categories),
            )
        final = None
        for attempt in range(MAX_RETRIES):
            try:
                final = analyze_chunks_in_one_request(
                    chunks,
                    assunto=analysis.assunto or "",
                    convidados=analysis.convidados or "",
                    prompt_version=analysis.prompt_version or "viral",
                    enforce_minimum=(attempt < MAX_RETRIES - 1),
                    allowed_theme_categories=allowed_theme_categories,
                    brand_only=brand_only,
                    analysis_id=analysis.id,
                )
                logger.info(
                    "[FLUXO] Grok API respondeu OK. candidates=%d, ranked_shorts=%d, final_long_cuts=%d",
                    len(final.get("candidate_shorts", [])),
                    len(final.get("ranked_shorts", [])),
                    len(final.get("final_long_cuts", [])),
                )
                break
            except Exception as e:
                logger.warning("[FLUXO] Grok API failed (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                if attempt < MAX_RETRIES - 1:
                    analysis.progress_message = (
                        f"Análise falhou (tentativa {attempt + 1}/{MAX_RETRIES}), repetindo..."
                    )
                    if not _safe_save_analysis(analysis, ["progress_message"]):
                        return
                else:
                    logger.exception("[FLUXO] Grok API failed after %d attempts", MAX_RETRIES)
                    analysis.status = "error"
                    analysis.error = "Falha na análise após 3 tentativas."
                    analysis.save(update_fields=["status", "error"])
                    return

        logger.info("[FLUXO] Saving suggestions and extracting cuts...")
        analysis.progress_message = "Extraindo cortes..."
        analysis.progress = 85
        if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
            return

        # Save suggestions and extract cuts
        AutoCutSuggestion.objects.filter(analysis=analysis).delete()
        AutoCutCorte = __import__("apps.auto_cuts.models", fromlist=["AutoCutCorte"]).AutoCutCorte
        from apps.auto_cuts.services.extract import extract_corte
        from apps.auto_cuts.services.thumbnail import generate_auto_thumbnail

        video_path = Path(analysis.video_file.path)
        media_root = Path(settings.MEDIA_ROOT)
        cortes_dir = media_root / "auto_cuts" / "cortes"
        cortes_dir.mkdir(parents=True, exist_ok=True)

        suggestions_created = []
        rank = 0
        is_viral_prompt = pv in ("viral", "viral_en", "viral_translate", "viral_long", "viral_long_en")
        is_educational_prompt = pv in ("educational", "educational_en")
        shorts_limit = max(1, min(10, int(getattr(analysis, "shorts_target", 10) or 10)))
        longs_limit = max(1, min(5, int(getattr(analysis, "longs_target", 3) or 3)))
        from apps.jobs.services.ffmpeg import seconds_to_tc, tc_to_seconds

        candidate_shorts_source = final.get("candidate_shorts") or []
        ranked_shorts_source = final.get("ranked_shorts") or []
        if is_viral_prompt:
            # For viral, prefer the larger pool (candidate_shorts), since ranked_shorts
            # can be partial even when many valid candidates exist.
            shorts_source = candidate_shorts_source or ranked_shorts_source
        else:
            shorts_source = ranked_shorts_source or candidate_shorts_source
        if pv in ("viral_long", "viral_long_en"):
            ranked_shorts = _sort_shorts_viral_long(shorts_source, tc_to_seconds)[:shorts_limit]
        else:
            ranked_shorts = _sort_by_virality(shorts_source)[:shorts_limit]
        ranked_longs = _sort_by_virality(final.get("final_long_cuts") or [])[:longs_limit]

        source_asset_id = ""
        if getattr(analysis, "source_id", None):
            source_asset_id = str(analysis.source_id)
        elif (analysis.youtube_url or "").strip():
            source_asset_id = (analysis.youtube_url or "").strip()
        elif getattr(analysis, "id", None):
            source_asset_id = f"analysis:{analysis.id}"

        # In factory context, skip items without valid category/mapping.
        ranked_shorts, shorts_ignored_missing_theme, shorts_ignored_unmapped = _filter_factory_routable_items(
            analysis, ranked_shorts
        )
        ranked_longs, longs_ignored_missing_theme, longs_ignored_unmapped = _filter_factory_routable_items(
            analysis, ranked_longs
        )
        ignored_total = (
            shorts_ignored_missing_theme
            + shorts_ignored_unmapped
            + longs_ignored_missing_theme
            + longs_ignored_unmapped
        )
        if ignored_total:
            logger.warning(
                "[FLUXO] Analysis %s: %s cut(s) skipped due to invalid theme_category / no mapping "
                "(shorts missing theme=%s, shorts unmapped=%s, longs missing theme=%s, longs unmapped=%s).",
                analysis.id,
                ignored_total,
                shorts_ignored_missing_theme,
                shorts_ignored_unmapped,
                longs_ignored_missing_theme,
                longs_ignored_unmapped,
            )

        for item in ranked_shorts:
            start_tc = _pick_timestamp(item, start=True)
            end_tc = _pick_timestamp(item, start=False)
            duration_seconds = item.get("duration") or item.get("duration_seconds")

            start_sec = tc_to_seconds(start_tc)
            end_sec = tc_to_seconds(end_tc)
            if end_sec <= start_sec:
                logger.info(
                    "[FLUXO] Invalid short skipped (end<=start): %s -> %s",
                    start_tc,
                    end_tc,
                )
                continue

            if is_viral_prompt:
                raw_duration = end_sec - start_sec
                if pv in ("viral_long", "viral_long_en"):
                    vmax = VIRAL_LONG_SHORT_MAX_SEC
                    vmin = VIRAL_LONG_SHORT_MIN_SEC
                    score_v = _normalize_virality_score(item.get("virality_score"))
                    if raw_duration < VIRAL_SHORT_MIN_SEC:
                        logger.info(
                            "[FLUXO] viral_long short skipped: duration %.2fs < absolute minimum %ss (%s -> %s)",
                            raw_duration,
                            VIRAL_SHORT_MIN_SEC,
                            start_tc,
                            end_tc,
                        )
                        continue
                    if raw_duration < vmin:
                        if score_v is not None and score_v > VIRAL_LONG_SHORT_SCORE_KEEP_IF_SHORT:
                            logger.info(
                                "[FLUXO] viral_long short kept (score=%s > %s) despite duration %.2fs < %ss (%s -> %s)",
                                score_v,
                                VIRAL_LONG_SHORT_SCORE_KEEP_IF_SHORT,
                                raw_duration,
                                vmin,
                                start_tc,
                                end_tc,
                            )
                        else:
                            logger.info(
                                "[FLUXO] viral_long short skipped: duration %.2fs < %ss and score <= %s (score=%s) (%s -> %s)",
                                raw_duration,
                                vmin,
                                VIRAL_LONG_SHORT_SCORE_KEEP_IF_SHORT,
                                score_v,
                                start_tc,
                                end_tc,
                            )
                            continue
                    if raw_duration > vmax:
                        end_sec = start_sec + vmax
                        end_tc = seconds_to_tc(end_sec)
                        raw_duration = vmax
                    duration_seconds = raw_duration
                else:
                    vmin, vmax = VIRAL_SHORT_MIN_SEC, VIRAL_SHORT_MAX_SEC
                    if raw_duration < vmin:
                        logger.info(
                            "[FLUXO] viral short skipped: duration < %ss: %.2fs (%s -> %s)",
                            vmin,
                            raw_duration,
                            start_tc,
                            end_tc,
                        )
                        continue
                    if raw_duration > vmax:
                        end_sec = start_sec + vmax
                        end_tc = seconds_to_tc(end_sec)
                        raw_duration = vmax
                    duration_seconds = raw_duration
            elif is_educational_prompt:
                raw_duration = end_sec - start_sec
                if raw_duration > EDUCATIONAL_SHORT_MAX_SEC:
                    end_sec = start_sec + EDUCATIONAL_SHORT_MAX_SEC
                    end_tc = seconds_to_tc(end_sec)
                    raw_duration = EDUCATIONAL_SHORT_MAX_SEC
                duration_seconds = raw_duration

            rank += 1
            brand_for_theme = getattr(analysis, "target_brand", None) or getattr(analysis, "brand", None)
            theme_for_suggestion = (
                (getattr(brand_for_theme, "theme_category", None) or "").strip()
                if brand_only and brand_for_theme
                else (item.get("theme_category") or "")
            )
            sug = AutoCutSuggestion.objects.create(
                analysis=analysis,
                cut_type="short",
                start_tc=start_tc,
                end_tc=end_tc,
                title=_append_convidados(
                    item.get("title") or item.get("suggested_title", ""),
                    analysis.convidados,
                ),
                reason=item.get("reason") or item.get("main_topic", ""),
                hook=item.get("hook") or item.get("hook_sentence", ""),
                virality_score=_normalize_virality_score(item.get("virality_score")),
                theme_category=theme_for_suggestion,
                source_asset_id=source_asset_id,
                rank=rank,
                duration_seconds=duration_seconds,
                raw_data=item,
            )
            suggestions_created.append((sug, "vertical"))

        for item in ranked_longs:
            start_tc = _pick_timestamp(item, start=True)
            end_tc = _pick_timestamp(item, start=False)
            start_sec = tc_to_seconds(start_tc)
            end_sec = tc_to_seconds(end_tc)
            if end_sec <= start_sec:
                logger.info(
                    "[FLUXO] Invalid long skipped (end<=start): %s -> %s",
                    start_tc,
                    end_tc,
                )
                continue
            if is_viral_prompt:
                raw_duration = end_sec - start_sec
                if raw_duration < VIRAL_LONG_MIN_SEC:
                    logger.info(
                        "[FLUXO] viral long skipped: duration < %ss: %.2fs (%s -> %s)",
                        VIRAL_LONG_MIN_SEC,
                        raw_duration,
                        start_tc,
                        end_tc,
                    )
                    continue
                if raw_duration > VIRAL_LONG_MAX_SEC:
                    end_sec = start_sec + VIRAL_LONG_MAX_SEC
                    end_tc = seconds_to_tc(end_sec)
                    raw_duration = VIRAL_LONG_MAX_SEC
                duration_minutes = round(raw_duration / 60.0, 2)
            else:
                duration_minutes = item.get("duration_min")

            brand_for_theme = getattr(analysis, "target_brand", None) or getattr(analysis, "brand", None)
            theme_for_long = (
                (getattr(brand_for_theme, "theme_category", None) or "").strip()
                if brand_only and brand_for_theme
                else (item.get("theme_category") or "")
            )
            sug = AutoCutSuggestion.objects.create(
                analysis=analysis,
                cut_type="long",
                start_tc=start_tc,
                end_tc=end_tc,
                title=_append_convidados(
                    item.get("title_suggestion") or item.get("suggested_title") or item.get("title", ""),
                    analysis.convidados,
                ),
                reason=item.get("reason") or item.get("main_topic", ""),
                virality_score=_normalize_virality_score(item.get("virality_score")),
                theme_category=theme_for_long,
                source_asset_id=source_asset_id,
                duration_minutes=duration_minutes,
                raw_data=item,
            )
            suggestions_created.append((sug, "horizontal"))

        logger.info("[FLUXO] %d suggestions created. Starting video extraction...", len(suggestions_created))
        # 6. Extract video for each suggestion and create AutoCutCorte
        total_cortes = len(suggestions_created)
        for i, (sug, fmt) in enumerate(suggestions_created):
            analysis.progress_message = f"Extraindo corte {i + 1}/{total_cortes}..."
            analysis.progress = 85 + int(10 * (i + 1) / total_cortes)
            if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
                logger.info("[FLUXO] Analysis %s deleted during extraction; aborting.", analysis_id)
                return

            out_path = cortes_dir / f"job_{analysis.id}_sug_{sug.id}.mp4"
            try:
                logger.info("[FLUXO] Extracting cut %d/%d: %s -> %s", i + 1, total_cortes, sug.start_tc, sug.end_tc)
                extract_corte(video_path, sug.start_tc, sug.end_tc, out_path, use_gpu=False)
            except Exception as e:
                analysis.status = "error"
                analysis.error = f"Erro ao extrair corte {i + 1}: {e}"
                analysis.save(update_fields=["status", "error"])
                return

            cut_start_sec = tc_to_seconds(sug.start_tc)
            cut_end_sec = tc_to_seconds(sug.end_tc)
            cut_duration = cut_end_sec - cut_start_sec
            raw_item = getattr(sug, "raw_data", None) or {}
            subtitle_segments_pt = raw_item.get("subtitle_segments_pt") if isinstance(raw_item, dict) else []
            if pv == "viral_translate" and subtitle_segments_pt:
                # Use Grok-translated subtitles (absolute timestamps → relative to cut)
                subtitle_segments = []
                for seg in subtitle_segments_pt:
                    s_start = float(seg.get("start", 0))
                    s_end = float(seg.get("end", 0))
                    if s_end <= cut_start_sec or s_start >= cut_end_sec:
                        continue
                    new_start = max(0.0, s_start - cut_start_sec)
                    new_end = min(cut_duration, s_end - cut_start_sec)
                    text = (seg.get("text") or "").strip()
                    if text:
                        subtitle_segments.append({"start": new_start, "end": new_end, "text": text})
            else:
                transcript_segments = analysis.transcript_segments or []
                subtitle_segments = []
                for seg in transcript_segments:
                    s_start = seg.get("start", 0)
                    s_end = seg.get("end", 0)
                    if s_end <= cut_start_sec or s_start >= cut_end_sec:
                        continue
                    new_start = max(0.0, s_start - cut_start_sec)
                    new_end = min(cut_duration, s_end - cut_start_sec)
                    text = (seg.get("text") or "").strip()
                    if text:
                        subtitle_segments.append({"start": new_start, "end": new_end, "text": text})

            # Shorts and longs: burned subtitles by default
            corte = AutoCutCorte.objects.create(
                analysis=analysis,
                suggestion=sug,
                format=fmt,
                needs_subtitle=True,
                # Factory-first flow: cuts enter automatic finalization.
                user_wants_finalize=True,
                is_finalized=False,
                subtitle_segments=subtitle_segments,
            )
            with open(out_path, "rb") as f:
                corte.file.save(out_path.name, File(f), save=True)
            target_brand = _resolve_target_brand_for_suggestion(analysis, sug)
            generated_thumb = generate_auto_thumbnail(corte, target_brand=target_brand)
            if generated_thumb:
                logger.info("[FLUXO] Auto thumbnail generated for cut %s.", corte.id)
            else:
                logger.info("[FLUXO] Auto thumbnail unavailable for cut %s.", corte.id)

        logger.info("[FLUXO] All %d cuts extracted. Queueing finalization.", total_cortes)
        if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
            logger.info("[FLUXO] Analysis %s deleted before final save; ignoring.", analysis_id)
            return
        _queue_analysis_finalization(analysis)
        logger.info("[FLUXO] Task completed successfully.")

    except Exception as e:
        if isinstance(e, DatabaseError) and "did not affect any rows" in str(e):
            logger.info("[FLUXO] Analysis %s deleted during processing; aborting.", analysis_id)
            return
        transcription_failures_total.labels(workload_type=_t_workload).inc()
        log_event(
            logger,
            event="transcription_finished",
            queue_name=_t_queue,
            workload_type=_t_workload,
            task_id=_t_task_id,
            duration_ms=_t_timer.elapsed_ms(),
            status="error",
            error=str(e),
            source_video_id=analysis_id,
        )
        AutoCutAnalysis.objects.filter(id=analysis_id).update(
            status="error",
            error=str(e),
            updated_at=timezone.now(),
        )
        raise


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
