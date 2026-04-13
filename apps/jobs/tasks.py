import logging
import os
import tempfile
from pathlib import Path

from celery import shared_task
from django.conf import settings

from apps.common.metrics import (
    render_duration_ms,
    render_failures_total,
    render_jobs_total,
    transcription_duration_ms,
    transcription_failures_total,
    transcription_jobs_total,
)
from apps.common.task_observability import resolve_task_observation_labels

from . import tasks_auto_fetch  # noqa: F401 - registra check_and_fetch_new_videos_task
from .logging_utils import Timer, ensure_job_correlation_id, log_event
from .services.dead_letter import create_dead_letter_job
from .services.ffmpeg import has_nvenc
from .services.pipeline import run_job
from .services.pipeline_execution import (
    STAGE_JOB_PROCESSING,
    STAGE_SUBTITLE_BURN,
    STAGE_TRANSCRIPTION,
    begin_transcription_stage_or_skip,
    complete_stage,
    fail_stage,
    get_or_create_job_pipeline_execution,
    mark_pipeline_completed,
    mark_pipeline_failed,
    start_stage,
)
from .services.subtitles import (
    burn_subtitles,
    generate_subtitles,
    segments_to_ass_animated,
    segments_to_srt,
)

logger = logging.getLogger(__name__)


def _whisper_workload_type() -> str:
    """Return 'cpu' or 'gpu' based on the same device-selection logic as generate_subtitles."""
    env_device = os.getenv("WHISPER_DEVICE", "").strip().lower()
    force_cpu = getattr(settings, "WHISPER_FORCE_CPU", False) or env_device == "cpu"
    return "cpu" if force_cpu else "gpu"


def _resolve_task_context(task) -> tuple[str, str]:
    return resolve_task_observation_labels(
        task=task,
        task_name=getattr(task, "name", "") or None,
    )


def _fail_stage_and_pipeline(
    pipeline_execution,
    *,
    stage_name: str,
    error: Exception | None = None,
    error_class: str = "",
    error_message: str = "",
) -> None:
    stage_execution = fail_stage(
        pipeline_execution,
        stage_name=stage_name,
        error=error,
        error_class=error_class,
        error_message=error_message,
    )
    mark_pipeline_failed(
        pipeline_execution,
        current_stage=stage_name,
        failure_reason=stage_execution.error_message,
    )
    try:
        create_dead_letter_job(
            pipeline_execution=pipeline_execution,
            stage_execution=stage_execution,
            aggregate_type=pipeline_execution.aggregate_type,
            aggregate_id=pipeline_execution.aggregate_id,
            job_name=stage_execution.task_name,
            queue_name=stage_execution.queue_name,
            correlation_id=pipeline_execution.correlation_id,
            error_class=stage_execution.error_class,
            error_message=stage_execution.error_message,
        )
    except Exception:
        logger.exception(
            "dead_letter_persistence_failed",
            extra={
                "aggregate_type": getattr(pipeline_execution, "aggregate_type", ""),
                "aggregate_id": getattr(pipeline_execution, "aggregate_id", 0),
                "stage_name": stage_name,
            },
        )


@shared_task(bind=True)
def process_job(self, job_id: int) -> None:
    from .models import Job

    job = Job.objects.get(id=job_id)
    ensure_job_correlation_id(job)
    pipeline_execution, _ = get_or_create_job_pipeline_execution(job)
    task_name, queue_name = _resolve_task_context(self)
    start_stage(
        pipeline_execution,
        stage_name=STAGE_JOB_PROCESSING,
        queue_name=queue_name,
        task_name=task_name,
        input_payload={"job_id": job.id},
    )

    try:
        run_job(job_id)
    except Exception as exc:
        _fail_stage_and_pipeline(
            pipeline_execution,
            stage_name=STAGE_JOB_PROCESSING,
            error=exc,
        )
        raise

    complete_stage(
        pipeline_execution,
        stage_name=STAGE_JOB_PROCESSING,
        output_payload={"job_status": "DONE"},
    )
    mark_pipeline_completed(
        pipeline_execution,
        current_stage=STAGE_JOB_PROCESSING,
    )


@shared_task(bind=True)
def generate_subtitles_task(self, job_id: int) -> None:
    from .models import Job

    job = Job.objects.get(id=job_id)
    ensure_job_correlation_id(job)
    pipeline_execution, _ = get_or_create_job_pipeline_execution(job)
    task_name, queue_name = _resolve_task_context(self)
    queue_name = queue_name or settings.CELERY_QUEUE_TRANSCRIPTION
    if not begin_transcription_stage_or_skip(
        pipeline_execution,
        queue_name=queue_name,
        task_name=task_name,
        job_id=job.id,
    ):
        logger.info(
            "transcription_skipped_duplicate_or_in_flight",
            extra={
                "source_video_id": job.id,
                "task_id": self.request.id or "",
            },
        )
        return

    try:
        out = job.output
        if not out or not out.file:
            job.subtitle_status = "error"
            job.subtitle_error = "Arquivo de vídeo não encontrado."
            job.save(update_fields=["subtitle_status", "subtitle_error"])
            _fail_stage_and_pipeline(
                pipeline_execution,
                stage_name=STAGE_TRANSCRIPTION,
                error_class="StageValidationError",
                error_message=job.subtitle_error,
            )
            return
        video_path = Path(out.file.path)
        if not video_path.exists():
            job.subtitle_status = "error"
            job.subtitle_error = "Arquivo não existe no disco."
            job.save(update_fields=["subtitle_status", "subtitle_error"])
            _fail_stage_and_pipeline(
                pipeline_execution,
                stage_name=STAGE_TRANSCRIPTION,
                error_class="StageValidationError",
                error_message=job.subtitle_error,
            )
            return
    except Exception as e:
        job.subtitle_status = "error"
        job.subtitle_error = str(e)
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        _fail_stage_and_pipeline(
            pipeline_execution,
            stage_name=STAGE_TRANSCRIPTION,
            error=e,
        )
        raise

    job.subtitle_status = "generating"
    job.subtitle_error = ""
    job.save(update_fields=["subtitle_status", "subtitle_error"])

    _queue = queue_name
    _workload = _whisper_workload_type()
    _task_id = self.request.id or ""
    log_event(
        logger,
        event="transcription_started",
        queue_name=_queue,
        workload_type=_workload,
        task_id=_task_id,
        status="started",
        source_video_id=job.id,
    )
    transcription_jobs_total.labels(workload_type=_workload).inc()
    _timer = Timer()
    try:
        segments = generate_subtitles(video_path, language="pt")
        job.subtitle_segments = segments
        job.subtitle_status = "ready_for_edit"
        job.subtitle_error = ""
        job.save(update_fields=["subtitle_segments", "subtitle_status", "subtitle_error"])
        complete_stage(
            pipeline_execution,
            stage_name=STAGE_TRANSCRIPTION,
            output_payload={
                "segments_count": len(segments),
                "subtitle_status": job.subtitle_status,
            },
        )
        transcription_duration_ms.labels(workload_type=_workload).observe(_timer.elapsed_ms())
        log_event(
            logger,
            event="transcription_finished",
            queue_name=_queue,
            workload_type=_workload,
            task_id=_task_id,
            duration_ms=_timer.elapsed_ms(),
            status="success",
            source_video_id=job.id,
        )
    except Exception as e:
        transcription_failures_total.labels(workload_type=_workload).inc()
        log_event(
            logger,
            event="transcription_finished",
            queue_name=_queue,
            workload_type=_workload,
            task_id=_task_id,
            duration_ms=_timer.elapsed_ms(),
            status="error",
            error=str(e),
            source_video_id=job.id,
        )
        job.subtitle_status = "error"
        job.subtitle_error = str(e)
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        _fail_stage_and_pipeline(
            pipeline_execution,
            stage_name=STAGE_TRANSCRIPTION,
            error=e,
        )
        raise


@shared_task(bind=True)
def burn_subtitles_task(self, job_id: int) -> None:
    import shutil

    from .models import Job

    job = Job.objects.get(id=job_id)
    ensure_job_correlation_id(job)
    pipeline_execution, _ = get_or_create_job_pipeline_execution(job)
    task_name, queue_name = _resolve_task_context(self)
    queue_name = queue_name or settings.CELERY_QUEUE_RENDER
    start_stage(
        pipeline_execution,
        stage_name=STAGE_SUBTITLE_BURN,
        queue_name=queue_name,
        task_name=task_name,
        input_payload={"job_id": job.id},
    )

    try:
        out = job.output
        if not out or not out.file:
            job.subtitle_status = "error"
            job.subtitle_error = "Arquivo de vídeo não encontrado."
            job.save(update_fields=["subtitle_status", "subtitle_error"])
            _fail_stage_and_pipeline(
                pipeline_execution,
                stage_name=STAGE_SUBTITLE_BURN,
                error_class="StageValidationError",
                error_message=job.subtitle_error,
            )
            return
        segments = job.subtitle_segments
        if not segments:
            job.subtitle_status = "error"
            job.subtitle_error = "Nenhum segmento de legenda."
            job.save(update_fields=["subtitle_status", "subtitle_error"])
            _fail_stage_and_pipeline(
                pipeline_execution,
                stage_name=STAGE_SUBTITLE_BURN,
                error_class="StageValidationError",
                error_message=job.subtitle_error,
            )
            return
    except Exception as e:
        job.subtitle_status = "error"
        job.subtitle_error = str(e)
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        _fail_stage_and_pipeline(
            pipeline_execution,
            stage_name=STAGE_SUBTITLE_BURN,
            error=e,
        )
        raise

    job.subtitle_status = "burning"
    job.subtitle_error = ""
    job.save(update_fields=["subtitle_status", "subtitle_error"])

    video_path = Path(out.file.path)
    if not video_path.exists():
        job.subtitle_status = "error"
        job.subtitle_error = "Arquivo não existe no disco."
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        _fail_stage_and_pipeline(
            pipeline_execution,
            stage_name=STAGE_SUBTITLE_BURN,
            error_class="StageValidationError",
            error_message=job.subtitle_error,
        )
        return

    _queue = queue_name
    _workload = "gpu" if has_nvenc() else "cpu"
    _task_id = self.request.id or ""
    log_event(
        logger,
        event="render_started",
        queue_name=_queue,
        workload_type=_workload,
        task_id=_task_id,
        status="started",
        job_id=job.id,
    )
    render_jobs_total.labels(workload_type=_workload).inc()
    _timer = Timer()
    try:
        animated = (job.subtitle_style or {}).get("animated", False)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            if animated:
                subs_path = tmppath / "subtitles.ass"
                subs_path.write_text(
                    segments_to_ass_animated(segments), encoding="utf-8"
                )
            else:
                subs_path = tmppath / "subtitles.srt"
                subs_path.write_text(segments_to_srt(segments), encoding="utf-8")
            output_tmp = tmppath / "output_with_subs.mp4"
            burn_subtitles(
                video_path,
                subs_path,
                output_tmp,
                job.subtitle_style,
                segments=segments,
            )
            media_root = Path(settings.MEDIA_ROOT)
            final_dir = media_root / "exports"
            final_dir.mkdir(parents=True, exist_ok=True)
            final_name = f"job_{job.id}_subs.mp4"
            final_path = final_dir / final_name
            shutil.copy2(output_tmp, final_path)
        out.file.delete(save=False)
        out.file.name = f"exports/{final_name}"
        out.save()
        job.subtitle_status = "burned"
        job.subtitle_error = ""
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        complete_stage(
            pipeline_execution,
            stage_name=STAGE_SUBTITLE_BURN,
            output_payload={
                "output_file": out.file.name,
                "subtitle_status": job.subtitle_status,
            },
        )
        mark_pipeline_completed(
            pipeline_execution,
            current_stage=STAGE_SUBTITLE_BURN,
        )
        render_duration_ms.labels(workload_type=_workload).observe(_timer.elapsed_ms())
        log_event(
            logger,
            event="render_finished",
            queue_name=_queue,
            workload_type=_workload,
            task_id=_task_id,
            duration_ms=_timer.elapsed_ms(),
            status="success",
            job_id=job.id,
        )
    except Exception as e:
        render_failures_total.labels(workload_type=_workload).inc()
        log_event(
            logger,
            event="render_finished",
            queue_name=_queue,
            workload_type=_workload,
            task_id=_task_id,
            duration_ms=_timer.elapsed_ms(),
            status="error",
            error=str(e),
            job_id=job.id,
        )
        job.subtitle_status = "error"
        job.subtitle_error = str(e)
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        _fail_stage_and_pipeline(
            pipeline_execution,
            stage_name=STAGE_SUBTITLE_BURN,
            error=e,
        )
        raise
