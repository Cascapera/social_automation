"""Celery tasks do Multiple-Creator (Fase 5+).

Esta fase entrega apenas a transcricao unica do submit. O fanout por brand
(criar N AutoCutAnalysis filhas + render) chega na Fase 6.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from apps.auto_cuts.services.transcript import segments_to_transcript_with_timestamps
from apps.auto_cuts.services.video_chunks import (
    cleanup_cortes_processo,
    extract_chunks_to_folder,
    transcribe_single_chunk,
)
from apps.auto_cuts.tasks import CHUNKED_TRANSCRIPTION_THRESHOLD_SEC
from apps.common.metrics import (
    transcription_duration_ms,
    transcription_failures_total,
    transcription_jobs_total,
)
from apps.jobs.logging_utils import Timer, log_event
from apps.jobs.services.ffmpeg import ffprobe_duration
from apps.jobs.services.subtitles import generate_subtitles

logger = logging.getLogger(__name__)


def _job_video_path(job) -> Path | None:
    """Resolve o caminho do video a transcrever para um MultipleCreatorJob.

    Prioridade: file enviado direto > source.file (SourceVideo). Para YOUTUBE,
    o download deve ter ocorrido antes desta funcao e populado job.file.
    """
    if job.file:
        try:
            return Path(job.file.path)
        except Exception:
            return None
    src = getattr(job, "source", None)
    if src and getattr(src, "file", None):
        try:
            return Path(src.file.path)
        except Exception:
            return None
    return None


def _download_youtube_to_job(job) -> Path | None:
    """Baixa o video do YouTube em job.youtube_url e salva em job.file."""
    from apps.auto_cuts.services.youtube_download import download_youtube

    media_root = Path(settings.MEDIA_ROOT)
    download_dir = media_root / "multiple_creator" / "sources"
    download_dir.mkdir(parents=True, exist_ok=True)
    out_path = download_dir / f"yt_mc_{job.id}.mp4"
    downloaded = download_youtube(job.youtube_url, out_path)
    with open(downloaded, "rb") as f:
        job.file.save(downloaded.name, File(f), save=True)
    saved_path = Path(job.file.path)
    if downloaded.resolve() != saved_path.resolve() and downloaded.exists():
        try:
            downloaded.unlink()
        except Exception:
            pass
    return saved_path


def _transcribe_video(video_path: Path, language: str = "pt") -> list[dict]:
    """Transcreve o video usando o mesmo pipeline do auto_cuts.

    Vai chunked para videos longos (limite igual ao analyze_auto_cuts_task).
    Retorna lista de segmentos {start, end, text}.
    """
    duration_sec = ffprobe_duration(video_path)
    if duration_sec > CHUNKED_TRANSCRIPTION_THRESHOLD_SEC:
        chunk_paths = extract_chunks_to_folder(
            video_path, f"mc_{video_path.stem}", chunk_minutes=18, overlap_minutes=3
        )
        boundaries = [(s, e) for _, s, e in chunk_paths]
        from apps.jobs.services.subtitles import load_whisper_model

        whisper_model, _ = load_whisper_model(
            model_size=os.getenv("WHISPER_MODEL", "small").strip() or "small",
            device=None,
        )
        all_segments: list[dict] = []
        try:
            for i, (chunk_path, start_sec, end_sec) in enumerate(chunk_paths):
                chunk = transcribe_single_chunk(
                    whisper_model, chunk_path, start_sec, end_sec, language=language
                )
                prev_end = boundaries[i - 1][1] if i > 0 else 0
                segs = [
                    {"start": s.get("start"), "end": s.get("end"), "text": s.get("text", "").strip()}
                    for s in chunk["segments"]
                    if s.get("start", 0) >= prev_end
                ]
                all_segments.extend(segs)
        finally:
            cleanup_cortes_processo(f"mc_{video_path.stem}")
        all_segments.sort(key=lambda s: s.get("start", 0))
        return all_segments
    return generate_subtitles(video_path, language=language)


@shared_task(bind=True)
def multiple_creator_transcribe_task(self, job_id: int) -> None:
    """Transcreve o video do MultipleCreatorJob uma unica vez.

    Apos sucesso, status -> READY (a Fase 6 fara o fanout). Em falha, status -> ERROR.
    Idempotente: se status nao for PENDING_TRANSCRIPTION, retorna.
    """
    from .models import MultipleCreatorJob

    try:
        job = MultipleCreatorJob.objects.get(pk=job_id)
    except ObjectDoesNotExist:
        return

    if job.status != "PENDING_TRANSCRIPTION":
        logger.info(
            "[MC] job %s no status %s; pulando transcribe (idempotencia).",
            job_id,
            job.status,
        )
        return

    job.status = "TRANSCRIBING"
    job.progress = 5
    job.progress_message = (
        "Baixando vídeo do YouTube..." if job.source_kind == "YOUTUBE" else "Transcrevendo vídeo..."
    )
    job.error = ""
    job.save(update_fields=["status", "progress", "progress_message", "error", "updated_at"])

    queue = settings.CELERY_QUEUE_TRANSCRIPTION
    workload = "cpu" if getattr(settings, "WHISPER_FORCE_CPU", True) else "gpu"
    task_id = self.request.id or ""
    log_event(
        logger,
        event="multiple_creator_transcription_started",
        queue_name=queue,
        workload_type=workload,
        task_id=task_id,
        status="started",
        multi_creator_job_id=job_id,
        source_kind=job.source_kind,
    )
    transcription_jobs_total.labels(workload_type=workload).inc()
    timer = Timer()

    try:
        if job.source_kind == "YOUTUBE":
            if not (job.youtube_url or "").strip():
                raise ValueError("youtube_url vazio para job YOUTUBE.")
            _download_youtube_to_job(job)
            job.refresh_from_db(fields=["file"])

        video_path = _job_video_path(job)
        if not video_path or not video_path.exists():
            raise FileNotFoundError("Arquivo de vídeo não encontrado no disco para transcrição.")

        prompt_lower = (job.prompt_version or "").lower()
        language = "en" if prompt_lower in (
            "viral_en", "viral_long_en", "educational_en", "viral_translate"
        ) else "pt"

        segments = _transcribe_video(video_path, language=language)
        if not segments:
            raise RuntimeError("Transcrição vazia.")

        job.transcript_segments = segments
        job.transcript = segments_to_transcript_with_timestamps(segments)
        job.status = "READY"
        job.progress = 20
        job.progress_message = "Transcrição concluída. Pronto para fanout."
        job.save(
            update_fields=[
                "transcript_segments",
                "transcript",
                "status",
                "progress",
                "progress_message",
                "updated_at",
            ]
        )

        transcription_duration_ms.labels(workload_type=workload).observe(timer.elapsed_ms())
        log_event(
            logger,
            event="multiple_creator_transcription_finished",
            queue_name=queue,
            workload_type=workload,
            task_id=task_id,
            duration_ms=timer.elapsed_ms(),
            status="success",
            multi_creator_job_id=job_id,
            segments_count=len(segments),
        )
        # Fan-out por brand uma vez que a transcricao terminou.
        multiple_creator_fanout_task.delay(job_id)
    except Exception as exc:
        logger.exception("[MC] transcribe falhou para job %s", job_id)
        transcription_failures_total.labels(workload_type=workload).inc()
        log_event(
            logger,
            event="multiple_creator_transcription_finished",
            queue_name=queue,
            workload_type=workload,
            task_id=task_id,
            duration_ms=timer.elapsed_ms(),
            status="error",
            multi_creator_job_id=job_id,
            error=str(exc),
        )
        job.status = "ERROR"
        job.error = f"Falha na transcrição: {exc}"
        job.progress_message = "Erro na transcrição."
        job.save(update_fields=["status", "error", "progress_message", "updated_at"])


def _build_autocut_for_execution(job, execution):
    """Cria uma AutoCutAnalysis filha vinculada a esta BrandExecution.

    Reaproveita transcript_segments, file/source e metadados do job.
    target_brand=execution.brand forca o roteamento de 100% dos cortes para a
    brand daquela execucao (ignora theme_category da IA).
    """
    from apps.auto_cuts.models import AutoCutAnalysis

    analysis = AutoCutAnalysis(
        user=job.user,
        brand=execution.brand,
        target_brand=execution.brand,
        distribution_mode="theme",
        source=job.source,
        youtube_url="",
        name=job.name or "",
        assunto=job.assunto or "",
        convidados=job.convidados or "",
        prompt_version=(job.prompt_version or "viral").strip().lower(),
        thumbnail_font=(job.thumbnail_font or "impact"),
        thumbnail_band_color=(job.thumbnail_band_color or "#E12E20"),
        thumbnail_text_color=(job.thumbnail_text_color or "#0A0A0A"),
        thumbnail_stroke_color=(job.thumbnail_stroke_color or "#FFEBDC"),
        shorts_target=job.shorts_target or 12,
        longs_target=job.longs_target or 3,
        vertical_mode=(job.vertical_mode or "zoom_crop"),
        transcript=job.transcript or "",
        transcript_segments=job.transcript_segments or [],
        status="pending",
        progress=0,
    )
    if job.file:
        # FileField.name guarda o path relativo; nao copia o arquivo no disco.
        analysis.file.name = job.file.name
    analysis.save()
    return analysis


def _dispatch_brand_execution(job, execution):
    """Cria a AutoCutAnalysis filha, linka a execution e enfileira a analise.

    A criacao da analysis + linkagem da execution roda em uma unica transacao
    para garantir que o curto-circuito em analyze_auto_cuts_task ja consiga
    detectar a origem multi-creator quando a task comecar.
    """
    from apps.auto_cuts.tasks import analyze_auto_cuts_task

    with transaction.atomic():
        analysis = _build_autocut_for_execution(job, execution)
        execution.auto_cut_analysis = analysis
        execution.status = "ANALYZING"
        execution.started_at = timezone.now()
        execution.error = ""
        execution.finished_at = None
        execution.save(
            update_fields=[
                "auto_cut_analysis",
                "status",
                "started_at",
                "error",
                "finished_at",
                "updated_at",
            ]
        )
    analyze_auto_cuts_task.delay(analysis.id)
    return analysis


@shared_task(bind=True)
def multiple_creator_fanout_task(self, job_id: int) -> None:
    """Fanout do Multiple-Creator (Fase 6): cria N AutoCutAnalysis filhas.

    Roda apenas quando job.status == READY (idempotente). Cada BrandExecution
    PENDING ganha uma AutoCutAnalysis com target_brand fixo, transcript ja
    populado e enfileira analyze_auto_cuts_task. Move o job para RUNNING_BRANDS.
    """
    from .models import MultipleCreatorJob

    try:
        job = MultipleCreatorJob.objects.get(pk=job_id)
    except ObjectDoesNotExist:
        return

    if job.status != "READY":
        logger.info(
            "[MC] fanout: job %s no status %s (esperado READY); pulando.",
            job_id,
            job.status,
        )
        return

    pending = list(job.brand_executions.filter(status="PENDING").select_related("brand"))
    log_event(
        logger,
        event="multiple_creator_fanout_started",
        status="started",
        multi_creator_job_id=job_id,
        brand_count=len(pending),
        task_id=self.request.id or "",
    )

    if not pending:
        job.status = "DONE"
        job.progress = 100
        job.progress_message = "Nenhuma execucao pendente."
        job.save(update_fields=["status", "progress", "progress_message", "updated_at"])
        log_event(
            logger,
            event="multiple_creator_fanout_finished",
            status="success",
            multi_creator_job_id=job_id,
            spawned_count=0,
        )
        return

    spawned = 0
    failures = 0
    for execution in pending:
        try:
            _dispatch_brand_execution(job, execution)
            spawned += 1
        except Exception as exc:
            failures += 1
            logger.exception(
                "[MC] fanout: falha ao criar filha para job=%s brand=%s",
                job_id,
                execution.brand_id,
            )
            execution.status = "ERROR"
            execution.error = f"Falha no fanout: {exc}"
            execution.finished_at = timezone.now()
            execution.save(
                update_fields=["status", "error", "finished_at", "updated_at"]
            )

    job.status = "RUNNING_BRANDS"
    job.progress = 30
    job.progress_message = f"Fanout iniciado: {spawned} brand(s)."
    job.save(update_fields=["status", "progress", "progress_message", "updated_at"])

    log_event(
        logger,
        event="multiple_creator_fanout_finished",
        status="success" if failures == 0 else "partial",
        multi_creator_job_id=job_id,
        spawned_count=spawned,
        failure_count=failures,
    )
