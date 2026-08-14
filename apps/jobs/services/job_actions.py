"""Ações sobre jobs: arquivar, deletar, remover output."""

import logging

from apps.jobs.logging_utils import log_event
from apps.jobs.models import Job, RenderOutput

logger = logging.getLogger(__name__)


def has_pending_scheduled_posts(job: Job) -> bool:
    """Retorna True se há agendamentos pendentes (PENDING ou POSTING)."""
    return job.scheduled_posts.filter(status__in=("PENDING", "POSTING")).exists()


def delete_job_output(job: Job) -> bool:
    """Remove o arquivo de export do job e o registro RenderOutput. Retorna True se removeu.

    Tolerante a falha de propósito: quem chama está arquivando ou apagando um job e precisa
    terminar. O que a falha não pode ser é invisível (R-20) — sem o log, o `.mp4` exportado
    ficava no disco sem registro nenhum de que sobrou.
    """
    try:
        out = RenderOutput.objects.filter(job=job).first()
        if out:
            if out.file:
                out.file.delete(save=False)
            out.delete()
            return True
    except Exception as e:
        log_event(
            logger,
            event="media_delete_failed",
            status="error",
            error=str(e),
            operation="delete_job_output",
            target="render_output",
            job_id=getattr(job, "id", None),
        )
    return False


def archive_job(job: Job) -> None:
    """Arquiva o job e remove o arquivo exportado."""
    delete_job_output(job)
    job.archived = True
    job.save(update_fields=["archived"])


def delete_job(job: Job) -> None:
    """Deleta o job e o arquivo exportado. Registros de agendamento são preservados (job_id=SET_NULL)."""
    delete_job_output(job)
    job.delete()
