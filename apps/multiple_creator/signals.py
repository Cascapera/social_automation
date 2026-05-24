"""Signals para fechar BrandExecution quando a AutoCutAnalysis filha conclui.

Quando o pipeline padrao do auto_cuts marca a analysis como done/error, este
signal propaga o estado para a MultipleCreatorBrandExecution vinculada e
recalcula o status agregado do MultipleCreatorJob (DONE / PARTIAL / ERROR /
RUNNING_BRANDS). Tambem emite contadores Prometheus e o evento
multiple_creator_completed quando o job vira terminal.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.common.metrics import (
    multiple_creator_brand_executions_total,
    multiple_creator_duration_ms,
    multiple_creator_jobs_total,
)
from apps.jobs.logging_utils import log_event

logger = logging.getLogger(__name__)

_TERMINAL_AUTO_CUT_STATES = ("done", "error")
_TERMINAL_BRAND_STATES = ("DONE", "ERROR")
_TERMINAL_JOB_STATES = ("DONE", "PARTIAL", "ERROR")


def _update_job_aggregate(job) -> None:
    statuses = list(job.brand_executions.values_list("status", flat=True))
    if not statuses:
        return
    if not all(s in _TERMINAL_BRAND_STATES for s in statuses):
        done_count = sum(1 for s in statuses if s in _TERMINAL_BRAND_STATES)
        total = len(statuses)
        progress = 30 + int(65 * done_count / total)
        msg = f"Processando brands: {done_count}/{total} concluída(s)."
        job.status = "RUNNING_BRANDS"
        job.progress = progress
        job.progress_message = msg
        job.save(update_fields=["status", "progress", "progress_message", "updated_at"])
        return

    if all(s == "DONE" for s in statuses):
        new_status = "DONE"
        new_progress = 100
        msg = "Concluido."
    elif all(s == "ERROR" for s in statuses):
        new_status = "ERROR"
        new_progress = 100
        msg = "Todas as brands falharam."
    else:
        new_status = "PARTIAL"
        new_progress = 100
        msg = "Concluido com falhas parciais."

    was_terminal = job.status in _TERMINAL_JOB_STATES
    if job.status != new_status:
        job.status = new_status
        job.progress = new_progress
        job.progress_message = msg
        job.save(update_fields=["status", "progress", "progress_message", "updated_at"])

    # Emitir contador + evento + histogram apenas na primeira transicao para terminal.
    # Retry granular pode reabrir o job (RUNNING_BRANDS) e fecha-lo de novo; queremos
    # contar cada fechamento.
    if not was_terminal:
        success = sum(1 for s in statuses if s == "DONE")
        failure = sum(1 for s in statuses if s == "ERROR")
        total_ms = 0
        if job.created_at:
            total_ms = max(0, int((timezone.now() - job.created_at).total_seconds() * 1000))
        multiple_creator_jobs_total.labels(result=new_status).inc()
        multiple_creator_duration_ms.observe(total_ms)
        log_event(
            logger,
            event="multiple_creator_completed",
            multi_creator_job_id=job.id,
            status=new_status.lower(),
            success_count=success,
            failure_count=failure,
            total_duration_ms=total_ms,
        )


@receiver(post_save, sender="auto_cuts.AutoCutAnalysis")
def close_brand_execution_on_analysis_finish(sender, instance, created, **kwargs):
    if created:
        return
    if instance.status not in _TERMINAL_AUTO_CUT_STATES:
        return
    from .models import MultipleCreatorBrandExecution

    execution = (
        MultipleCreatorBrandExecution.objects
        .select_related("job")
        .filter(auto_cut_analysis_id=instance.id)
        .first()
    )
    if not execution:
        return

    new_brand_status = "DONE" if instance.status == "done" else "ERROR"
    fields_to_update = ["status", "finished_at", "updated_at"]
    execution.status = new_brand_status
    execution.finished_at = timezone.now()
    if instance.status == "error":
        execution.error = (instance.error or "")[:5000]
        fields_to_update.append("error")
    execution.save(update_fields=fields_to_update)
    multiple_creator_brand_executions_total.labels(result=new_brand_status).inc()

    try:
        _update_job_aggregate(execution.job)
    except Exception:
        logger.exception(
            "[MC signal] falha ao recalcular agregado do job %s", execution.job_id
        )
