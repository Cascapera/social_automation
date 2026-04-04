from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.jobs.models import DeadLetterJob, PipelineExecution, StageExecution

SUPPORTED_REPLAY_JOB_NAMES = frozenset(
    {
        "process_job",
        "generate_subtitles_task",
        "burn_subtitles_task",
    }
)


def _normalize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return payload or {}


def _normalize_job_name(job_name: str) -> str:
    value = str(job_name or "").strip()
    if not value:
        return ""
    return value.rsplit(".", 1)[-1]


def classify_dead_letter_error(
    *,
    error: Exception | None = None,
    error_class: str = "",
    error_message: str = "",
) -> str:
    resolved_error_class = (error_class or (error.__class__.__name__ if error is not None else "")).strip()
    resolved_error_message = (error_message or (str(error) if error is not None else "")).strip()
    class_lower = resolved_error_class.lower()
    message_lower = resolved_error_message.lower()

    if resolved_error_class == "StageValidationError":
        return DeadLetterJob.ErrorCategory.NON_RETRYABLE

    if any(
        marker in message_lower
        for marker in (
            "arquivo de vídeo não encontrado",
            "arquivo não existe no disco",
            "nenhum segmento de legenda",
            "job precisa de pelo menos",
            "not found",
            "does not exist",
        )
    ):
        return DeadLetterJob.ErrorCategory.NON_RETRYABLE

    if any(
        marker in class_lower
        for marker in (
            "timeout",
            "connection",
            "operational",
            "database",
            "redis",
            "amqp",
        )
    ) or any(
        marker in message_lower
        for marker in (
            "timeout",
            "connection",
            "database",
            "postgres",
            "redis",
            "broker",
            "temporary failure",
        )
    ):
        return DeadLetterJob.ErrorCategory.INFRA

    if any(
        marker in class_lower
        for marker in (
            "decode",
            "parse",
            "json",
            "validation",
        )
    ) or any(
        marker in message_lower
        for marker in (
            "corrupt",
            "malformed",
            "decode",
            "invalid payload",
            "invalid media",
        )
    ):
        return DeadLetterJob.ErrorCategory.POISON

    if "retryable" in class_lower or any(
        marker in message_lower
        for marker in (
            "retryable",
            "try again",
            "retry later",
            "temporarily unavailable",
        )
    ):
        return DeadLetterJob.ErrorCategory.RETRYABLE

    return DeadLetterJob.ErrorCategory.UNKNOWN


def _build_payload_snapshot(
    *,
    pipeline_execution: PipelineExecution | None,
    stage_execution: StageExecution | None,
    payload_json: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(_normalize_payload(payload_json))
    if stage_execution is not None:
        payload.setdefault("stage_name", stage_execution.stage_name)
        payload.setdefault("task_name", stage_execution.task_name)
        payload.setdefault("input_payload", stage_execution.input_payload or {})
    if pipeline_execution is not None:
        payload.setdefault("pipeline_type", pipeline_execution.pipeline_type)
    return payload


def create_dead_letter_job(
    *,
    pipeline_execution: PipelineExecution | None = None,
    stage_execution: StageExecution | None = None,
    aggregate_type: str,
    aggregate_id: int,
    job_name: str,
    queue_name: str = "",
    correlation_id: str = "",
    payload_json: dict[str, Any] | None = None,
    error_class: str = "",
    error_message: str = "",
    error_category: str = "",
) -> DeadLetterJob:
    resolved_job_name = _normalize_job_name(
        job_name or getattr(stage_execution, "task_name", "")
    )
    if not resolved_job_name:
        raise ValueError("job_name is required to create a dead letter entry.")

    resolved_queue_name = (queue_name or getattr(stage_execution, "queue_name", "")).strip()
    resolved_correlation_id = (
        correlation_id or getattr(pipeline_execution, "correlation_id", "")
    ).strip()
    resolved_error_class = (
        error_class or getattr(stage_execution, "error_class", "")
    ).strip()
    resolved_error_message = (
        error_message or getattr(stage_execution, "error_message", "")
    ).strip()
    resolved_error_category = (
        error_category
        or classify_dead_letter_error(
            error_class=resolved_error_class,
            error_message=resolved_error_message,
        )
    )
    payload_snapshot = _build_payload_snapshot(
        pipeline_execution=pipeline_execution,
        stage_execution=stage_execution,
        payload_json=payload_json,
    )
    now = timezone.now()

    with transaction.atomic():
        queryset = DeadLetterJob.objects.select_for_update().filter(
            status=DeadLetterJob.Status.OPEN,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            job_name=resolved_job_name,
        )
        if stage_execution is not None:
            queryset = queryset.filter(stage_execution_id=stage_execution.pk)
        elif pipeline_execution is not None:
            queryset = queryset.filter(
                pipeline_execution_id=pipeline_execution.pk,
                stage_execution__isnull=True,
            )

        dead_letter = queryset.order_by("-id").first()
        if dead_letter is None:
            return DeadLetterJob.objects.create(
                pipeline_execution=pipeline_execution,
                stage_execution=stage_execution,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                job_name=resolved_job_name,
                queue_name=resolved_queue_name,
                correlation_id=resolved_correlation_id,
                payload_json=payload_snapshot,
                error_class=resolved_error_class,
                error_message=resolved_error_message,
                error_category=resolved_error_category,
                status=DeadLetterJob.Status.OPEN,
                retry_count=0,
                first_failed_at=now,
                last_failed_at=now,
                replay_result_json={},
            )

        dead_letter.pipeline_execution = pipeline_execution or dead_letter.pipeline_execution
        dead_letter.stage_execution = stage_execution or dead_letter.stage_execution
        dead_letter.queue_name = resolved_queue_name or dead_letter.queue_name
        dead_letter.correlation_id = resolved_correlation_id or dead_letter.correlation_id
        dead_letter.payload_json = payload_snapshot
        dead_letter.error_class = resolved_error_class
        dead_letter.error_message = resolved_error_message
        dead_letter.error_category = resolved_error_category
        dead_letter.last_failed_at = now
        dead_letter.retry_count += 1
        dead_letter.replay_result_json = {}
        dead_letter.save(
            update_fields=[
                "pipeline_execution",
                "stage_execution",
                "queue_name",
                "correlation_id",
                "payload_json",
                "error_class",
                "error_message",
                "error_category",
                "last_failed_at",
                "retry_count",
                "replay_result_json",
                "updated_at",
            ]
        )
        return dead_letter


def mark_dead_letter_replayed(
    dead_letter: DeadLetterJob,
    *,
    user=None,
    replay_result_json: dict[str, Any] | None = None,
) -> DeadLetterJob:
    now = timezone.now()
    with transaction.atomic():
        locked = DeadLetterJob.objects.select_for_update().get(pk=dead_letter.pk)
        if locked.status != DeadLetterJob.Status.OPEN:
            raise ValueError("Only open dead letters can be marked as replayed.")

        locked.status = DeadLetterJob.Status.REPLAYED
        locked.replayed_at = now
        locked.replayed_by = user if getattr(user, "pk", None) else None
        locked.replay_result_json = _normalize_payload(replay_result_json)
        locked.save(
            update_fields=[
                "status",
                "replayed_at",
                "replayed_by",
                "replay_result_json",
                "updated_at",
            ]
        )
        return locked


def mark_dead_letter_ignored(dead_letter: DeadLetterJob) -> DeadLetterJob:
    with transaction.atomic():
        locked = DeadLetterJob.objects.select_for_update().get(pk=dead_letter.pk)
        if locked.status != DeadLetterJob.Status.OPEN:
            raise ValueError("Only open dead letters can be ignored.")

        locked.status = DeadLetterJob.Status.IGNORED
        locked.save(update_fields=["status", "updated_at"])
        return locked


def _get_supported_replay_task(job_name: str):
    from apps.jobs.tasks import (
        burn_subtitles_task,
        generate_subtitles_task,
        process_job,
    )

    mapping = {
        "process_job": process_job,
        "generate_subtitles_task": generate_subtitles_task,
        "burn_subtitles_task": burn_subtitles_task,
    }
    return mapping.get(_normalize_job_name(job_name))


def _mark_replay_failure(dead_letter: DeadLetterJob, exc: Exception) -> DeadLetterJob:
    now = timezone.now()
    error_class = exc.__class__.__name__
    error_message = str(exc)
    replay_result_json = {
        "status": "dispatch_failed",
        "error_class": error_class,
        "error_message": error_message,
    }
    with transaction.atomic():
        locked = DeadLetterJob.objects.select_for_update().get(pk=dead_letter.pk)
        if locked.status == DeadLetterJob.Status.OPEN:
            locked.last_failed_at = now
            locked.error_class = error_class
            locked.error_message = error_message
            locked.error_category = classify_dead_letter_error(
                error_class=error_class,
                error_message=error_message,
            )
            locked.replay_result_json = replay_result_json
            locked.save(
                update_fields=[
                    "last_failed_at",
                    "error_class",
                    "error_message",
                    "error_category",
                    "replay_result_json",
                    "updated_at",
                ]
            )
        return locked


def replay_dead_letter_job(dead_letter: DeadLetterJob, user=None) -> DeadLetterJob:
    locked = DeadLetterJob.objects.get(pk=dead_letter.pk)
    try:
        with transaction.atomic():
            locked = DeadLetterJob.objects.select_for_update().get(pk=dead_letter.pk)
            if locked.status != DeadLetterJob.Status.OPEN:
                raise ValueError("Only open dead letters can be replayed.")

            normalized_job_name = _normalize_job_name(locked.job_name)
            replay_task = _get_supported_replay_task(normalized_job_name)
            if replay_task is None or normalized_job_name not in SUPPORTED_REPLAY_JOB_NAMES:
                raise ValueError(f"Unsupported dead letter job: {locked.job_name}")
            if not locked.aggregate_id:
                raise ValueError("Dead letter aggregate_id is required for replay.")

        async_result = replay_task.delay(locked.aggregate_id)
        replay_result_json = {
            "status": "dispatched",
            "task_id": getattr(async_result, "id", ""),
            "aggregate_id": locked.aggregate_id,
            "job_name": normalized_job_name,
        }
        return mark_dead_letter_replayed(
            locked,
            user=user,
            replay_result_json=replay_result_json,
        )
    except Exception as exc:
        _mark_replay_failure(locked, exc)
        raise
