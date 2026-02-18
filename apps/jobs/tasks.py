from celery import shared_task
from .services.pipeline import run_job

@shared_task(bind=True)
def process_job(self, job_id: int) -> None:
    run_job(job_id)
