"""Fluxo de análise de auto_cuts (refactor.md R-19 / D-11).

Este módulo é o destino do que era o corpo de `analyze_auto_cuts_task`, que tinha 652
linhas dentro de `apps/auto_cuts/tasks.py`. A task Celery continua lá — o nome dela é
contrato de fila e não pode mudar —, mas agora ela só delega para `run_analysis()`.

O fluxo está fatiado em etapas na ordem em que rodam:

  `run_analysis`            guardas de entrada (job apagado, idempotência, fila pausada,
                            lote de cortes prontos) e delegação
  `_analyze_and_cut`        o miolo, com o try/except que grava `status="error"`
  `_download_youtube_source`
  `_resolve_video_path`
  `_transcribe_into_analysis`
  `_request_llm_analysis`
  `_create_suggestions`
  `_extract_cuts_for_suggestions`

As etapas que podem **interromper o fluxo** devolvem um sinal em vez de levantar: `False`,
`None` ou `_ABORTED`. Quando isso acontece, a etapa **já gravou** o estado final da análise
(normalmente `status="error"` com a mensagem que vai para a tela) — o chamador só precisa
sair. Era assim que os `return` espalhados pela função original funcionavam.

Os fluxos de "cortes prontos" (`ready cuts`) moram aqui também: são ramos da análise,
chamados só por ela.

Nada aqui importa `tasks.py` no topo do módulo: a dependência é `tasks.py → services/`.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File
from django.db.utils import DatabaseError
from django.utils import timezone

from apps.auto_cuts.models import (
    AutoCutAnalysis,
    AutoCutCorte,
    AutoCutReadyChunk,
    AutoCutSuggestion,
)
from apps.auto_cuts.services.extract import extract_corte
from apps.auto_cuts.services.flow_common import (
    _append_convidados,
    _queue_analysis_finalization,
    _resolve_target_brand_for_suggestion,
    _safe_save_analysis,
    _sanitize_long_overlay_fk,
)
from apps.auto_cuts.services.grok import (
    analyze_chunks_in_one_request,
    analyze_ready_cut_metadata,
    analyze_ready_cuts_batch_titles_from_transcripts,
)
from apps.auto_cuts.services.thumbnail import generate_auto_thumbnail
from apps.auto_cuts.services.transcript import (
    chunk_transcript,
    segments_to_transcript_with_timestamps,
)
from apps.auto_cuts.services.video_chunks import (
    cleanup_cortes_processo,
    extract_chunks_to_folder,
    transcribe_single_chunk,
)
from apps.auto_cuts.services.youtube_download import download_youtube
from apps.brands.models import Brand, BrandCategory
from apps.common.metrics import (
    transcription_duration_ms,
    transcription_failures_total,
    transcription_jobs_total,
)
from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.services.ffmpeg import (
    concat_with_xfade,
    ffprobe_duration,
    has_nvenc,
    normalize_video_to_canvas,
    seconds_to_tc,
    tc_to_seconds,
)
from apps.jobs.services.subtitles import generate_subtitles, load_whisper_model

logger = logging.getLogger(__name__)

# Sentinela de interrupção: distingue "a etapa mandou parar" de "a etapa devolveu None".
# Sem isso, um None inesperado do LLM viraria uma saída silenciosa em vez do erro que a
# função original produzia.
_ABORTED = object()

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
        # Import adiado de propósito (R-19 d): o `except ImportError` é o contrato daqui —
        # sem o app Multiple-Creator instalado, a resposta é "não veio de lá", não erro.
        from apps.multiple_creator.models import MultipleCreatorBrandExecution
    except ImportError:
        return False
    return MultipleCreatorBrandExecution.objects.filter(
        auto_cut_analysis_id=analysis.id
    ).exists()


def run_analysis(task, analysis_id: int) -> None:
    """Corpo de `analyze_auto_cuts_task`: transcreve, analisa em blocos e extrai os cortes.

    `task` é o `self` da task Celery (`bind=True`) — usado só para reagendar a si mesma
    quando a factory está com a fila pausada.

    As saídas silenciosas daqui (entrega duplicada, job apagado) são de propósito: com
    `acks_late` elas são comportamento normal da fila, não excepcional.
    """
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
            task.apply_async(args=[analysis_id], countdown=60)
        logger.info("[FLUXO] Analysis %s deferred: factory has processing_paused.", analysis_id)
        return

    # Ready cuts: batch (multiple files → one job)
    if getattr(analysis, "is_ready_cuts", False):
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

    _analyze_and_cut(analysis, analysis_id, task_id=task.request.id or "")


def _analyze_and_cut(analysis, analysis_id: int, *, task_id: str) -> None:
    """Transcrição → análise no LLM → extração dos cortes → fila de finalização.

    O `except` do fim é o que transforma qualquer falha não tratada em `status="error"`
    com a mensagem crua e **re-levanta** — o Celery precisa ver a exceção.
    """
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
        if not _download_youtube_source(analysis, analysis_id, youtube_url):
            return

    video_path = _resolve_video_path(analysis)
    if video_path is None:
        return

    _t_queue = settings.CELERY_QUEUE_TRANSCRIPTION
    _t_workload = "cpu" if getattr(settings, "WHISPER_FORCE_CPU", True) else "gpu"
    _t_task_id = task_id
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

        if not _transcribe_into_analysis(
            analysis,
            analysis_id,
            video_path,
            multi_creator_skip=multi_creator_skip,
            use_chunked=use_chunked,
            transcript_lang=transcript_lang,
            workload=_t_workload,
        ):
            return

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

        final = _request_llm_analysis(analysis, chunks)
        if final is _ABORTED:
            return

        logger.info("[FLUXO] Saving suggestions and extracting cuts...")
        analysis.progress_message = "Extraindo cortes..."
        analysis.progress = 85
        if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
            return

        # Resolvidos aqui, antes de criar as sugestões, como na função original: o mkdir
        # do diretório de cortes acontece antes de qualquer linha no banco.
        cut_source_path = Path(analysis.video_file.path)
        cortes_dir = Path(settings.MEDIA_ROOT) / "auto_cuts" / "cortes"
        cortes_dir.mkdir(parents=True, exist_ok=True)

        suggestions_created = _create_suggestions(analysis, final, pv)
        total_cortes = len(suggestions_created)
        if not _extract_cuts_for_suggestions(
            analysis, analysis_id, suggestions_created, pv, cut_source_path, cortes_dir
        ):
            return

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


def _download_youtube_source(analysis, analysis_id: int, youtube_url: str) -> bool:
    """Baixa o vídeo do YouTube para `analysis.file`. False = o chamador deve sair."""
    analysis.progress_message = "Baixando vídeo do YouTube..."
    analysis.progress = 2
    if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
        return False
    try:
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
        return False
    return True


def _resolve_video_path(analysis) -> Path | None:
    """Caminho do vídeo de origem, ou None quando não dá para seguir.

    As duas mensagens de erro daqui são diferentes de propósito: "não anexou vídeo" e
    "o arquivo sumiu do disco" pedem ações opostas do usuário (reenviar × suporte).
    """
    video_file = analysis.video_file
    if not video_file:
        analysis.status = "error"
        analysis.error = "Nenhum vídeo encontrado (source ou upload)."
        analysis.save(update_fields=["status", "error"])
        return None

    video_path = Path(video_file.path)
    if not video_path.exists():
        analysis.status = "error"
        analysis.error = "Arquivo de vídeo não existe no disco."
        analysis.save(update_fields=["status", "error"])
        return None
    return video_path


def _transcribe_into_analysis(
    analysis,
    analysis_id: int,
    video_path: Path,
    *,
    multi_creator_skip: bool,
    use_chunked: bool,
    transcript_lang: str,
    workload: str,
) -> bool:
    """Preenche `transcript_segments`/`transcript`. False = o chamador deve sair.

    Três caminhos: pular (o Multiple-Creator já populou), fatiado (vídeo longo, evita OOM)
    ou passada única.
    """
    if multi_creator_skip:
        return True  # transcript_segments ja vieram populados do MultipleCreatorJob pai

    if use_chunked:
        # Flow: extract chunks → save under cortes_processo → transcribe one by one → delete chunk
        # Each chunk = 18 min (small files on disk, no huge temp in memory)
        try:
            analysis.progress_message = "Extraindo blocos de áudio..."
            if not _safe_save_analysis(analysis, ["progress_message"]):
                transcription_failures_total.labels(workload_type=workload).inc()
                return False
            chunk_paths = extract_chunks_to_folder(
                video_path, analysis.id,
                chunk_minutes=18, overlap_minutes=3)
            total_chunks = len(chunk_paths)
            all_segments = []
            boundaries = [(s, e) for _, s, e in chunk_paths]

            # Simple loop (no generator) — avoids crash when exiting generator on long videos
            _whisper_model, _ = load_whisper_model(model_size=os.getenv("WHISPER_MODEL", "small").strip() or "small", device=None)

            for i, (chunk_path, start_sec, end_sec) in enumerate(chunk_paths):
                analysis.progress_message = f"Transcrevendo bloco {i + 1}/{total_chunks}..."
                analysis.progress = 5 + int(15 * (i + 1) / total_chunks)
                if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
                    logger.info("[FLUXO] Analysis %s deleted during transcription; aborting.", analysis_id)
                    transcription_failures_total.labels(workload_type=workload).inc()
                    return False
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
        return True

    # Original flow: transcribe whole video
    segments = generate_subtitles(video_path, language=transcript_lang)
    if not segments:
        analysis.status = "error"
        analysis.error = "Nenhum segmento transcrito."
        analysis.save(update_fields=["status", "error"])
        transcription_failures_total.labels(workload_type=workload).inc()
        return False

    analysis.transcript_segments = segments
    analysis.transcript = segments_to_transcript_with_timestamps(segments)
    logger.info("[FLUXO] Single-pass transcription done.")
    return True


def _request_llm_analysis(analysis, chunks):
    """Chama o LLM até 3 vezes. Devolve o payload, ou `_ABORTED` se o chamador deve sair.

    A última tentativa vai com `enforce_minimum=False`: é melhor aceitar menos cortes do
    que perder a análise inteira.
    """
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
                    return _ABORTED
            else:
                logger.exception("[FLUXO] Grok API failed after %d attempts", MAX_RETRIES)
                analysis.status = "error"
                analysis.error = "Falha na análise após 3 tentativas."
                analysis.save(update_fields=["status", "error"])
                return _ABORTED
    return final


def _create_suggestions(analysis, final: dict, pv: str) -> list[tuple]:
    """Cria as `AutoCutSuggestion` a partir da resposta do LLM.

    Aplica ordenação, limites por tipo de prompt, corte de duração e o filtro de
    roteamento da factory. Devolve pares `(suggestion, formato)` na ordem de extração.
    """
    # Save suggestions and extract cuts
    AutoCutSuggestion.objects.filter(analysis=analysis).delete()

    suggestions_created = []
    rank = 0
    brand_only = _is_brand_only(analysis)
    is_viral_prompt = pv in ("viral", "viral_en", "viral_translate", "viral_long", "viral_long_en")
    is_educational_prompt = pv in ("educational", "educational_en")
    shorts_limit = max(1, min(10, int(getattr(analysis, "shorts_target", 10) or 10)))
    longs_limit = max(1, min(5, int(getattr(analysis, "longs_target", 3) or 3)))

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
    return suggestions_created


def _extract_cuts_for_suggestions(
    analysis,
    analysis_id: int,
    suggestions_created: list[tuple],
    pv: str,
    video_path: Path,
    cortes_dir: Path,
) -> bool:
    """Extrai o vídeo de cada sugestão e cria o `AutoCutCorte`. False = o chamador deve sair."""
    # 6. Extract video for each suggestion and create AutoCutCorte
    total_cortes = len(suggestions_created)
    for i, (sug, fmt) in enumerate(suggestions_created):
        analysis.progress_message = f"Extraindo corte {i + 1}/{total_cortes}..."
        analysis.progress = 85 + int(10 * (i + 1) / total_cortes)
        if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
            logger.info("[FLUXO] Analysis %s deleted during extraction; aborting.", analysis_id)
            return False

        out_path = cortes_dir / f"job_{analysis.id}_sug_{sug.id}.mp4"
        try:
            logger.info("[FLUXO] Extracting cut %d/%d: %s -> %s", i + 1, total_cortes, sug.start_tc, sug.end_tc)
            extract_corte(video_path, sug.start_tc, sug.end_tc, out_path, use_gpu=False)
        except Exception as e:
            analysis.status = "error"
            analysis.error = f"Erro ao extrair corte {i + 1}: {e}"
            analysis.save(update_fields=["status", "error"])
            return False

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

    return True


def _process_ready_cuts_flow(analysis, duration_sec: float, segments: list) -> None:
    """
    Ready-cuts flow: video already edited.
    Transcribe, call LLM for metadata (title, thumbnail), copy video without re-extract,
    generate thumbnail, and finalize.
    """
    analysis.status = "analyzing"
    analysis.progress_message = "Analisando metadata com IA..."
    analysis.progress = 20
    analysis.save(update_fields=["status", "progress_message", "progress"])

    transcript = analysis.transcript or ""
    tl = getattr(analysis, "ready_cuts_titles_language", None) or "pt"
    if tl not in ("pt", "en"):
        tl = "pt"
    metadata = analyze_ready_cut_metadata(transcript, duration_sec, titles_language=tl)
    title = _append_convidados(metadata.get("title") or "Vídeo", analysis.convidados)
    # LLM returns 1–10; normalize to 0–100 (scale used elsewhere)
    raw_score = metadata.get("virality_score") or 5
    virality_score = max(0, min(100, int(float(raw_score)) * 10)) if raw_score is not None else 50
    raw_data = {
        "thumbnail_moment_timestamp": metadata.get("thumbnail_moment_timestamp") or "00:00",
        "thumbnail_text": metadata.get("thumbnail_text") or "Vídeo",
    }

    analysis.progress_message = "Criando corte e thumbnail..."
    analysis.progress = 70
    analysis.save(update_fields=["progress_message", "progress"])

    video_path = Path(analysis.video_file.path)
    media_root = Path(settings.MEDIA_ROOT)
    cortes_dir = media_root / "auto_cuts" / "cortes"
    cortes_dir.mkdir(parents=True, exist_ok=True)

    AutoCutSuggestion.objects.filter(analysis=analysis).delete()
    end_tc = seconds_to_tc(duration_sec)
    sug = AutoCutSuggestion.objects.create(
        analysis=analysis,
        cut_type="short",
        start_tc="00:00",
        end_tc=end_tc,
        title=title,
        reason="",
        hook="",
        virality_score=virality_score,
        theme_category="",
        source_asset_id=f"analysis:{analysis.id}",
        rank=1,
        duration_seconds=duration_sec,
        raw_data=raw_data,
    )

    out_path = cortes_dir / f"job_{analysis.id}_sug_{sug.id}.mp4"
    shutil.copy(video_path, out_path)

    transcript_segments = analysis.transcript_segments or []
    subtitle_segments = [
        {"start": s.get("start", 0), "end": s.get("end", 0), "text": (s.get("text") or "").strip()}
        for s in transcript_segments
        if (s.get("text") or "").strip()
    ]

    corte = AutoCutCorte.objects.create(
        analysis=analysis,
        suggestion=sug,
        format="vertical",
        needs_subtitle=True,
        user_wants_finalize=True,
        is_finalized=False,
        subtitle_segments=subtitle_segments,
    )
    with open(out_path, "rb") as f:
        corte.file.save(out_path.name, File(f), save=True)

    target_brand = _resolve_target_brand_for_suggestion(analysis, sug)
    generate_auto_thumbnail(corte, target_brand=target_brand)

    _queue_analysis_finalization(analysis)
    logger.info("[FLUXO] Ready cuts flow completed successfully.")


def _merge_subtitle_segments_for_xfade(
    chunk_durations: list[float],
    fade_duration: float,
    segments_per_chunk: list[list[dict]],
) -> list[dict]:
    """Adjust segment timestamps to merged long video with xfade (same as concat_with_xfade)."""
    out = []
    fade = float(fade_duration)
    for i, segs in enumerate(segments_per_chunk):
        offset = sum(chunk_durations[j] for j in range(i)) - i * fade
        for s in segs or []:
            txt = (s.get("text") or "").strip()
            if not txt:
                continue
            st = offset + float(s.get("start", 0))
            en = offset + float(s.get("end", 0))
            if en < st:
                st, en = en, st
            out.append({"start": max(0.0, st), "end": max(0.0, en), "text": txt})
    out.sort(key=lambda x: x["start"])
    return out


def _base_name_for_ready_cuts_no_transcript(chunks: list, analysis) -> str:
    """Base name without transcript: job name (required on upload); else first file stem; else Job #id."""
    job = (getattr(analysis, "name", None) or "").strip()
    if job:
        return job
    first = chunks[0] if chunks else None
    base = ""
    if first and getattr(first, "file", None) and getattr(first.file, "name", None):
        base = Path(first.file.name).stem
    base = (base or "").strip()
    if not base:
        base = f"Job {getattr(analysis, 'id', '')}"
    return " ".join(base.replace("_", " ").split())


def _titles_for_ready_cuts_no_transcript(chunks: list, analysis) -> dict[str, str]:
    """Without transcript: '{job name} Part 1', 'Part 2', ... (no LLM)."""
    base = _base_name_for_ready_cuts_no_transcript(chunks, analysis)
    return {str(i): f"{base} Part {i + 1}"[:200] for i in range(len(chunks))}


def _process_ready_cuts_batch_flow(analysis_id: int) -> None:
    """
    Multiple files in one job: queued transcription, titles (LLM), optional long video (fade),
    then shorts; automatic finalization.
    """
    analysis = AutoCutAnalysis.objects.filter(id=analysis_id).first()
    if not analysis:
        return

    chunks_qs = AutoCutReadyChunk.objects.filter(analysis=analysis).order_by("order_index", "id")
    chunks = list(chunks_qs)
    if not chunks:
        analysis.status = "error"
        analysis.error = "Nenhum arquivo no lote de cortes prontos."
        analysis.save(update_fields=["status", "error"])
        return

    transcribe = bool(getattr(analysis, "ready_cuts_transcribe", True))
    fade_d = float(getattr(analysis, "ready_cuts_long_fade_duration", None) or 0.5)
    fade_d = max(0.1, min(3.0, fade_d))
    create_long = bool(getattr(analysis, "ready_cuts_create_long_video", False))
    titles_lang = getattr(analysis, "ready_cuts_titles_language", None) or "pt"
    if titles_lang not in ("pt", "en"):
        titles_lang = "pt"
    pv = (analysis.prompt_version or "viral").strip().lower()
    transcript_lang = "en" if pv in ("viral_en", "viral_long_en", "educational_en", "viral_translate") else "pt"

    analysis.status = "transcribing" if transcribe else "analyzing"
    analysis.progress_message = "Transcrevendo cortes..." if transcribe else "Medindo vídeos e gerando títulos..."
    analysis.progress = 8
    analysis.error = ""
    analysis.save(update_fields=["status", "progress_message", "progress", "error"])

    media_root = Path(settings.MEDIA_ROOT)
    cortes_dir = media_root / "auto_cuts" / "cortes"
    cortes_dir.mkdir(parents=True, exist_ok=True)

    for i, ch in enumerate(chunks):
        vp = Path(ch.file.path)
        if not vp.exists():
            analysis.status = "error"
            analysis.error = f"Arquivo ausente no chunk {i + 1}."
            analysis.save(update_fields=["status", "error"])
            return
        dur = ffprobe_duration(vp)
        ch.duration_seconds = dur
        to_update = ["duration_seconds"]
        if transcribe:
            analysis.progress_message = f"Transcrevendo vídeo {i + 1}/{len(chunks)}..."
            analysis.progress = 8 + int(35 * (i + 1) / max(len(chunks), 1))
            analysis.save(update_fields=["progress_message", "progress"])
            segs = generate_subtitles(vp, language=transcript_lang)
            if not segs:
                segs = []
            ch.transcript_segments = segs
            ch.transcript = segments_to_transcript_with_timestamps(segs) if segs else ""
            to_update += ["transcript_segments", "transcript"]
        ch.save(update_fields=to_update)

    analysis.progress_message = (
        "Gerando títulos com IA..."
        if transcribe
        else "Definindo títulos (nome do job + Part 1, 2, ...)..."
    )
    analysis.progress = 48
    analysis.status = "analyzing"
    analysis.save(update_fields=["progress_message", "progress", "status"])

    title_by_index: dict[str, str] = {}
    if transcribe:
        items = []
        for i, ch in enumerate(chunks):
            items.append({"id": str(i), "transcript": (ch.transcript or "")[:14000]})
        title_by_index = analyze_ready_cuts_batch_titles_from_transcripts(
            items, titles_language=titles_lang
        )
    else:
        title_by_index = _titles_for_ready_cuts_no_transcript(chunks, analysis)

    for i in range(len(chunks)):
        if str(i) in title_by_index and (title_by_index[str(i)] or "").strip():
            continue
        ch = chunks[i]
        if transcribe and (ch.transcript or "").strip():
            md = analyze_ready_cut_metadata(
                ch.transcript or "",
                float(ch.duration_seconds or 0),
                titles_language=titles_lang,
            )
            title_by_index[str(i)] = (md.get("title") or f"Vídeo {i + 1}")[:200]
        else:
            base = _base_name_for_ready_cuts_no_transcript(chunks, analysis)
            title_by_index[str(i)] = f"{base} Part {i + 1}"[:200]

    AutoCutSuggestion.objects.filter(analysis=analysis).delete()

    rank_counter = 1

    def _thumb_text_from_title(title: str) -> str:
        words = (title or "").replace("\n", " ").split()
        return (" ".join(words[:4]).upper()[:28] or "DESTAQUE")

    if create_long:
        analysis.progress_message = "Montando vídeo longo..."
        analysis.progress = 55
        analysis.save(update_fields=["progress_message", "progress"])
        parts = [Path(ch.file.path) for ch in chunks]
        long_path = cortes_dir / f"job_{analysis.id}_long_concat.mp4"
        try:
            # Normalize each clip to 1920×1080 (16:9) with letterbox/pillarbox for xfade
            # to accept mixed H/V and resolutions.
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)
                normalized_paths: list[Path] = []
                _gpu = has_nvenc()
                for i, p in enumerate(parts):
                    np = tmpdir_path / f"norm_{i}.mp4"
                    # Unified FPS/SAR/audio: xfade needs matching timebase between clips (e.g. 60 vs 25 fps).
                    normalize_video_to_canvas(
                        p, np, use_gpu=_gpu, target_fps=30, audio_hz=48000
                    )
                    normalized_paths.append(np)
                if len(normalized_paths) == 1:
                    shutil.copy(normalized_paths[0], long_path)
                else:
                    tmp_out = tmpdir_path / "long.mp4"
                    concat_with_xfade(normalized_paths, tmp_out, "fade", fade_d, _gpu)
                    shutil.copy(tmp_out, long_path)
        except Exception as e:
            logger.exception("[FLUXO] Failed to assemble long video: %s", e)
            analysis.status = "error"
            analysis.error = f"Erro ao montar vídeo longo: {e}"
            analysis.save(update_fields=["status", "error"])
            return

        long_dur = ffprobe_duration(long_path)
        merged_subs = []
        if transcribe:
            durs = [float(ch.duration_seconds or 0) for ch in chunks]
            segs_list = [ch.transcript_segments or [] for ch in chunks]
            merged_subs = _merge_subtitle_segments_for_xfade(durs, fade_d, segs_list)

        job_title = (analysis.name or "").strip() or "Vídeo longo"
        long_raw = {
            "thumbnail_moment_timestamp": "00:02",
            "thumbnail_text": _thumb_text_from_title(job_title),
        }
        long_sug = AutoCutSuggestion.objects.create(
            analysis=analysis,
            cut_type="long",
            start_tc="00:00",
            end_tc=seconds_to_tc(long_dur),
            title=job_title[:200],
            reason="",
            hook="",
            virality_score=70,
            theme_category="",
            source_asset_id=f"analysis:{analysis.id}:long",
            rank=rank_counter,
            duration_seconds=long_dur,
            duration_minutes=long_dur / 60.0,
            raw_data=long_raw,
        )
        rank_counter += 1
        long_corte = AutoCutCorte.objects.create(
            analysis=analysis,
            suggestion=long_sug,
            format="horizontal",
            needs_subtitle=transcribe,
            user_wants_finalize=True,
            is_finalized=False,
            subtitle_segments=merged_subs if transcribe else [],
        )
        with open(long_path, "rb") as f:
            long_corte.file.save(long_path.name, File(f), save=True)
        tb = _resolve_target_brand_for_suggestion(analysis, long_sug)
        generate_auto_thumbnail(long_corte, target_brand=tb)

    analysis.progress_message = "Criando cortes (shorts)..."
    analysis.progress = 72
    analysis.save(update_fields=["progress_message", "progress"])

    for i, ch in enumerate(chunks):
        title = _append_convidados(
            (title_by_index.get(str(i)) or "").strip() or f"Vídeo {i + 1}",
            analysis.convidados,
        )
        dsec = float(ch.duration_seconds or ffprobe_duration(Path(ch.file.path)))
        end_tc = seconds_to_tc(dsec)
        thumb_ts = min(4.5, max(0.5, min(dsec * 0.25, 5.0)))
        short_raw = {
            "thumbnail_moment_timestamp": "00:02",
            "thumbnail_text": _thumb_text_from_title(title),
            "thumbnail_frame_sec": thumb_ts,
        }
        sug = AutoCutSuggestion.objects.create(
            analysis=analysis,
            cut_type="short",
            start_tc="00:00",
            end_tc=end_tc,
            title=title[:200],
            reason="",
            hook="",
            virality_score=75,
            theme_category="",
            source_asset_id=f"ready_chunk:{ch.id}",
            rank=rank_counter,
            duration_seconds=dsec,
            raw_data=short_raw,
        )
        rank_counter += 1
        sub_seg = []
        if transcribe and ch.transcript_segments:
            sub_seg = [
                {"start": s.get("start", 0), "end": s.get("end", 0), "text": (s.get("text") or "").strip()}
                for s in ch.transcript_segments
                if (s.get("text") or "").strip()
            ]
        out_path = cortes_dir / f"job_{analysis.id}_sug_{sug.id}.mp4"
        shutil.copy(Path(ch.file.path), out_path)
        corte = AutoCutCorte.objects.create(
            analysis=analysis,
            suggestion=sug,
            format="vertical",
            needs_subtitle=transcribe,
            user_wants_finalize=True,
            is_finalized=False,
            subtitle_segments=sub_seg,
        )
        with open(out_path, "rb") as f:
            corte.file.save(out_path.name, File(f), save=True)
        tb = _resolve_target_brand_for_suggestion(analysis, sug)
        generate_auto_thumbnail(corte, target_brand=tb)

    _queue_analysis_finalization(analysis)
    logger.info("[FLUXO] Ready cuts batch completed (analysis=%s, long=%s).", analysis_id, bool(create_long))
