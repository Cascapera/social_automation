"""Ações sobre jobs: arquivar, deletar, remover output."""

from apps.jobs.models import Job, RenderOutput


def has_pending_scheduled_posts(job: Job) -> bool:
    """Retorna True se há agendamentos pendentes (PENDING ou POSTING)."""
    return job.scheduled_posts.filter(status__in=("PENDING", "POSTING")).exists()


def delete_job_output(job: Job) -> bool:
    """Remove o arquivo de export do job e o registro RenderOutput. Retorna True se removeu."""
    try:
        out = RenderOutput.objects.filter(job=job).first()
        if out:
            if out.file:
                out.file.delete(save=False)
            out.delete()
            return True
    except Exception:
        pass
    return False


def archive_job(job: Job) -> None:
    """Arquiva o job e remove o arquivo exportado."""
    delete_job_output(job)
    job.archived = True
    job.save(update_fields=["archived"])


def delete_job(job: Job) -> None:
    """Deleta o job e o arquivo exportado. Levanta ValueError se houver agendamento pendente."""
    if has_pending_scheduled_posts(job):
        raise ValueError("Não é possível deletar: há agendamento de postagem pendente.")
    delete_job_output(job)
    job.delete()
