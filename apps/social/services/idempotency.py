from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.jobs.models import IdempotencyKey

DEFAULT_IN_PROGRESS_TTL = timedelta(hours=6)


@dataclass(frozen=True)
class IdempotencyAcquireResult:
    outcome: str
    record: IdempotencyKey

    @property
    def should_execute(self) -> bool:
        return self.outcome == "acquired"


def _default_expires_at():
    return timezone.now() + DEFAULT_IN_PROGRESS_TTL


def _is_expired(record: IdempotencyKey) -> bool:
    return bool(record.expires_at and record.expires_at <= timezone.now())


def acquire_idempotency_key(
    *,
    key: str,
    operation_name: str,
    aggregate_type: str,
    aggregate_id: int,
    expires_at=None,
) -> IdempotencyAcquireResult:
    expires_at = expires_at or _default_expires_at()
    try:
        with transaction.atomic():
            record = IdempotencyKey.objects.create(
                key=key,
                operation_name=operation_name,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                status=IdempotencyKey.Status.IN_PROGRESS,
                expires_at=expires_at,
            )
            return IdempotencyAcquireResult(outcome="acquired", record=record)
    except IntegrityError:
        with transaction.atomic():
            record = IdempotencyKey.objects.select_for_update().get(key=key)
            if record.status == IdempotencyKey.Status.SUCCEEDED:
                return IdempotencyAcquireResult(outcome="succeeded", record=record)
            if record.status == IdempotencyKey.Status.IN_PROGRESS and not _is_expired(record):
                return IdempotencyAcquireResult(outcome="in_progress", record=record)
            record.operation_name = operation_name
            record.aggregate_type = aggregate_type
            record.aggregate_id = aggregate_id
            record.status = IdempotencyKey.Status.IN_PROGRESS
            record.result_payload = {}
            record.error_message = ""
            record.expires_at = expires_at
            record.save(
                update_fields=[
                    "operation_name",
                    "aggregate_type",
                    "aggregate_id",
                    "status",
                    "result_payload",
                    "error_message",
                    "expires_at",
                    "updated_at",
                ]
            )
            return IdempotencyAcquireResult(outcome="acquired", record=record)


def mark_idempotency_success(*, key: str, result_payload: dict | None = None) -> IdempotencyKey:
    with transaction.atomic():
        record = IdempotencyKey.objects.select_for_update().get(key=key)
        record.status = IdempotencyKey.Status.SUCCEEDED
        record.result_payload = result_payload or {}
        record.error_message = ""
        record.expires_at = None
        record.save(
            update_fields=[
                "status",
                "result_payload",
                "error_message",
                "expires_at",
                "updated_at",
            ]
        )
        return record


def mark_idempotency_failed(
    *,
    key: str,
    error_message: str,
    result_payload: dict | None = None,
) -> IdempotencyKey:
    with transaction.atomic():
        record = IdempotencyKey.objects.select_for_update().get(key=key)
        record.status = IdempotencyKey.Status.FAILED
        record.result_payload = result_payload or {}
        record.error_message = error_message[:2000]
        record.expires_at = None
        record.save(
            update_fields=[
                "status",
                "result_payload",
                "error_message",
                "expires_at",
                "updated_at",
            ]
        )
        return record


def get_existing_idempotency_result(key: str) -> dict | None:
    record = IdempotencyKey.objects.filter(
        key=key,
        status=IdempotencyKey.Status.SUCCEEDED,
    ).only("result_payload").first()
    if not record:
        return None
    return record.result_payload or {}
