from celery import shared_task

from .services import extract_cuts_from_source


@shared_task(bind=True)
def extract_cuts_task(self, source_id: int, cuts_data: list) -> list:
    """Extract cuts from source, save files, delete source. Returns created cut IDs."""
    created = extract_cuts_from_source(source_id, cuts_data)
    return [c.id for c in created]
