from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.jobs.models import Job, PipelineExecution, StageExecution

JOB_PIPELINE_TYPE = "job_pipeline"
JOB_AGGREGATE_TYPE = "job"

STAGE_JOB_PROCESSING = "job_processing"
STAGE_TRANSCRIPTION = "transcription"
STAGE_SUBTITLE_BURN = "subtitle_burn"


def _save_with_updated_at(instance, update_fields: list[str]) -> None:
    if "updated_at" not in update_fields:
        update_fields.append("updated_at")
    instance.save(update_fields=update_fields)


def _duration_ms(started_at, completed_at) -> int:
    return max(0, int((completed_at - started_at).total_seconds() * 1000))


def _normalize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return payload or {}


def _get_or_create_stage_locked(
    *,
    pipeline_execution: PipelineExecution,
    stage_name: str,
) -> tuple[StageExecution, bool]:
    lookup = {
        "pipeline_execution": pipeline_execution,
        "stage_name": stage_name,
    }
    try:
        return StageExecution.objects.select_for_update().get_or_create(**lookup)
    except IntegrityError:
        return StageExecution.objects.select_for_update().get(**lookup), False


def get_or_create_pipeline_execution(
    *,
    pipeline_type: str,
    aggregate_type: str,
    aggregate_id: int,
    correlation_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> tuple[PipelineExecution, bool]:
    lookup = {
        "pipeline_type": pipeline_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
    }
    pipeline_execution = (
        PipelineExecution.objects.filter(**lookup).order_by("-attempt_number").first()
    )
    created = False
    if pipeline_execution is None:
        try:
            pipeline_execution = PipelineExecution.objects.create(
                **lookup,
                attempt_number=1,
                correlation_id=correlation_id or "",
                status=PipelineExecution.Status.PENDING,
                metadata_json=_normalize_payload(metadata),
            )
            created = True
        except IntegrityError:
            pipeline_execution = (
                PipelineExecution.objects.filter(**lookup)
                .order_by("-attempt_number")
                .first()
            )

    update_fields: list[str] = []
    if correlation_id and pipeline_execution.correlation_id != correlation_id:
        pipeline_execution.correlation_id = correlation_id
        update_fields.append("correlation_id")

    if metadata:
        merged_metadata = dict(pipeline_execution.metadata_json or {})
        metadata_changed = False
        for key, value in metadata.items():
            if merged_metadata.get(key) != value:
                merged_metadata[key] = value
                metadata_changed = True
        if metadata_changed:
            pipeline_execution.metadata_json = merged_metadata
            update_fields.append("metadata_json")

    if update_fields:
        _save_with_updated_at(pipeline_execution, update_fields)

    return pipeline_execution, created


def begin_transcription_stage_or_skip(
    pipeline_execution: PipelineExecution,
    *,
    queue_name: str,
    task_name: str,
    job_id: int,
) -> bool:
    """
    Start the transcription stage or skip if another worker already owns it.

    Returns True if this invocation should run Whisper; False if the stage is
    already COMPLETED (idempotent no-op) or RUNNING (concurrent duplicate task).

    The check and start_stage run in one DB transaction with row locks so two
    parallel Celery processes cannot both pass the guard for the same job.
    """
    with transaction.atomic():
        pipeline_execution = PipelineExecution.objects.select_for_update().get(
            pk=pipeline_execution.pk
        )
        stage_execution, _ = _get_or_create_stage_locked(
            pipeline_execution=pipeline_execution,
            stage_name=STAGE_TRANSCRIPTION,
        )
        if stage_execution.status in (
            StageExecution.Status.COMPLETED,
            StageExecution.Status.RUNNING,
        ):
            return False
        start_stage(
            pipeline_execution,
            stage_name=STAGE_TRANSCRIPTION,
            queue_name=queue_name,
            task_name=task_name,
            input_payload={"job_id": job_id},
        )
        return True


def get_or_create_job_pipeline_execution(job: Job) -> tuple[PipelineExecution, bool]:
    metadata = {
        key: value
        for key, value in {
            "brand_id": job.brand_id,
            "user_id": job.user_id,
        }.items()
        if value is not None
    }
    return get_or_create_pipeline_execution(
        pipeline_type=JOB_PIPELINE_TYPE,
        aggregate_type=JOB_AGGREGATE_TYPE,
        aggregate_id=job.id,
        correlation_id=job.correlation_id,
        metadata=metadata,
    )


def start_new_pipeline_attempt(
    *,
    pipeline_type: str,
    aggregate_type: str,
    aggregate_id: int,
    correlation_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> PipelineExecution:
    """Cria uma nova tentativa (attempt_number = max+1) preservando histórico."""
    lookup = {
        "pipeline_type": pipeline_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
    }
    last_error: IntegrityError | None = None
    for _ in range(3):
        with transaction.atomic():
            last = (
                PipelineExecution.objects.filter(**lookup)
                .order_by("-attempt_number")
                .first()
            )
            next_attempt = (last.attempt_number + 1) if last else 1
            try:
                return PipelineExecution.objects.create(
                    **lookup,
                    attempt_number=next_attempt,
                    correlation_id=correlation_id or "",
                    status=PipelineExecution.Status.PENDING,
                    metadata_json=_normalize_payload(metadata),
                )
            except IntegrityError as exc:
                last_error = exc
                continue
    raise RuntimeError(
        "Falha ao alocar nova PipelineExecution após 3 tentativas"
    ) from last_error


def start_new_job_pipeline_attempt(job: Job) -> PipelineExecution:
    metadata = {
        key: value
        for key, value in {"brand_id": job.brand_id, "user_id": job.user_id}.items()
        if value is not None
    }
    return start_new_pipeline_attempt(
        pipeline_type=JOB_PIPELINE_TYPE,
        aggregate_type=JOB_AGGREGATE_TYPE,
        aggregate_id=job.id,
        correlation_id=job.correlation_id,
        metadata=metadata,
    )


def start_stage(
    pipeline_execution: PipelineExecution,
    *,
    stage_name: str,
    queue_name: str = "",
    task_name: str = "",
    input_payload: dict[str, Any] | None = None,
) -> StageExecution:
    now = timezone.now()
    with transaction.atomic():
        pipeline_execution = PipelineExecution.objects.select_for_update().get(
            pk=pipeline_execution.pk
        )
        stage_execution, created = _get_or_create_stage_locked(
            pipeline_execution=pipeline_execution,
            stage_name=stage_name,
        )

        if not created and stage_execution.status != StageExecution.Status.PENDING:
            stage_execution.retry_count += 1

        stage_execution.status = StageExecution.Status.RUNNING
        stage_execution.queue_name = queue_name or stage_execution.queue_name
        stage_execution.task_name = task_name or stage_execution.task_name
        stage_execution.started_at = now
        stage_execution.completed_at = None
        stage_execution.duration_ms = None
        stage_execution.input_payload = _normalize_payload(input_payload)
        stage_execution.output_payload = {}
        stage_execution.error_class = ""
        stage_execution.error_message = ""
        _save_with_updated_at(
            stage_execution,
            [
                "status",
                "queue_name",
                "task_name",
                "retry_count",
                "started_at",
                "completed_at",
                "duration_ms",
                "input_payload",
                "output_payload",
                "error_class",
                "error_message",
            ],
        )

        if pipeline_execution.started_at is None:
            pipeline_execution.started_at = now

        pipeline_execution.status = PipelineExecution.Status.RUNNING
        pipeline_execution.current_stage = stage_name
        pipeline_execution.completed_at = None
        pipeline_execution.failure_reason = ""
        _save_with_updated_at(
            pipeline_execution,
            [
                "status",
                "current_stage",
                "started_at",
                "completed_at",
                "failure_reason",
            ],
        )

    return stage_execution


def complete_stage(
    pipeline_execution: PipelineExecution,
    *,
    stage_name: str,
    output_payload: dict[str, Any] | None = None,
) -> StageExecution:
    now = timezone.now()
    with transaction.atomic():
        pipeline_execution = PipelineExecution.objects.select_for_update().get(
            pk=pipeline_execution.pk
        )
        stage_execution, _ = _get_or_create_stage_locked(
            pipeline_execution=pipeline_execution,
            stage_name=stage_name,
        )
        if stage_execution.started_at is None:
            stage_execution.started_at = now

        stage_execution.status = StageExecution.Status.COMPLETED
        stage_execution.completed_at = now
        stage_execution.duration_ms = _duration_ms(stage_execution.started_at, now)
        stage_execution.output_payload = _normalize_payload(output_payload)
        stage_execution.error_class = ""
        stage_execution.error_message = ""
        _save_with_updated_at(
            stage_execution,
            [
                "status",
                "started_at",
                "completed_at",
                "duration_ms",
                "output_payload",
                "error_class",
                "error_message",
            ],
        )

        pipeline_execution.current_stage = stage_name
        pipeline_execution.failure_reason = ""
        _save_with_updated_at(
            pipeline_execution,
            ["current_stage", "failure_reason"],
        )

    return stage_execution


def fail_stage(
    pipeline_execution: PipelineExecution,
    *,
    stage_name: str,
    error: Exception | None = None,
    error_class: str = "",
    error_message: str = "",
) -> StageExecution:
    now = timezone.now()
    with transaction.atomic():
        pipeline_execution = PipelineExecution.objects.select_for_update().get(
            pk=pipeline_execution.pk
        )
        stage_execution, _ = _get_or_create_stage_locked(
            pipeline_execution=pipeline_execution,
            stage_name=stage_name,
        )
        if stage_execution.started_at is None:
            stage_execution.started_at = now

        resolved_error_class = error_class or (
            error.__class__.__name__ if error is not None else ""
        )
        resolved_error_message = error_message or (
            str(error) if error is not None else ""
        )

        stage_execution.status = StageExecution.Status.FAILED
        stage_execution.completed_at = now
        stage_execution.duration_ms = _duration_ms(stage_execution.started_at, now)
        stage_execution.error_class = resolved_error_class
        stage_execution.error_message = resolved_error_message
        _save_with_updated_at(
            stage_execution,
            [
                "status",
                "started_at",
                "completed_at",
                "duration_ms",
                "error_class",
                "error_message",
            ],
        )

    return stage_execution


def mark_pipeline_completed(
    pipeline_execution: PipelineExecution,
    *,
    current_stage: str = "",
) -> PipelineExecution:
    now = timezone.now()
    with transaction.atomic():
        pipeline_execution = PipelineExecution.objects.select_for_update().get(
            pk=pipeline_execution.pk
        )
        if pipeline_execution.started_at is None:
            pipeline_execution.started_at = now

        pipeline_execution.status = PipelineExecution.Status.COMPLETED
        if current_stage:
            pipeline_execution.current_stage = current_stage
        pipeline_execution.completed_at = now
        pipeline_execution.failure_reason = ""
        _save_with_updated_at(
            pipeline_execution,
            [
                "status",
                "current_stage",
                "started_at",
                "completed_at",
                "failure_reason",
            ],
        )

    return pipeline_execution


def mark_pipeline_failed(
    pipeline_execution: PipelineExecution,
    *,
    current_stage: str = "",
    failure_reason: str = "",
) -> PipelineExecution:
    now = timezone.now()
    with transaction.atomic():
        pipeline_execution = PipelineExecution.objects.select_for_update().get(
            pk=pipeline_execution.pk
        )
        if pipeline_execution.started_at is None:
            pipeline_execution.started_at = now

        pipeline_execution.status = PipelineExecution.Status.FAILED
        if current_stage:
            pipeline_execution.current_stage = current_stage
        pipeline_execution.completed_at = now
        pipeline_execution.failure_reason = failure_reason
        _save_with_updated_at(
            pipeline_execution,
            [
                "status",
                "current_stage",
                "started_at",
                "completed_at",
                "failure_reason",
            ],
        )

    return pipeline_execution
