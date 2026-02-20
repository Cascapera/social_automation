import tempfile
from pathlib import Path

from celery import shared_task
from django.conf import settings

from .services.pipeline import run_job
from .services.subtitles import (
    generate_subtitles,
    segments_to_srt,
    segments_to_ass_animated,
    burn_subtitles,
)


@shared_task(bind=True)
def process_job(self, job_id: int) -> None:
    run_job(job_id)


@shared_task(bind=True)
def generate_subtitles_task(self, job_id: int) -> None:
    from .models import Job

    job = Job.objects.get(id=job_id)
    try:
        out = job.output
        if not out or not out.file:
            job.subtitle_status = "error"
            job.subtitle_error = "Arquivo de vídeo não encontrado."
            job.save(update_fields=["subtitle_status", "subtitle_error"])
            return
        video_path = Path(out.file.path)
        if not video_path.exists():
            job.subtitle_status = "error"
            job.subtitle_error = "Arquivo não existe no disco."
            job.save(update_fields=["subtitle_status", "subtitle_error"])
            return
    except Exception as e:
        job.subtitle_status = "error"
        job.subtitle_error = str(e)
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        raise

    job.subtitle_status = "generating"
    job.subtitle_error = ""
    job.save(update_fields=["subtitle_status", "subtitle_error"])

    try:
        segments = generate_subtitles(video_path, language="pt")
        job.subtitle_segments = segments
        job.subtitle_status = "ready_for_edit"
        job.subtitle_error = ""
        job.save(update_fields=["subtitle_segments", "subtitle_status", "subtitle_error"])
    except Exception as e:
        job.subtitle_status = "error"
        job.subtitle_error = str(e)
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        raise


@shared_task(bind=True)
def burn_subtitles_task(self, job_id: int) -> None:
    import shutil

    from .models import Job, RenderOutput

    job = Job.objects.get(id=job_id)
    try:
        out = job.output
        if not out or not out.file:
            job.subtitle_status = "error"
            job.subtitle_error = "Arquivo de vídeo não encontrado."
            job.save(update_fields=["subtitle_status", "subtitle_error"])
            return
        segments = job.subtitle_segments
        if not segments:
            job.subtitle_status = "error"
            job.subtitle_error = "Nenhum segmento de legenda."
            job.save(update_fields=["subtitle_status", "subtitle_error"])
            return
    except Exception as e:
        job.subtitle_status = "error"
        job.subtitle_error = str(e)
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        raise

    job.subtitle_status = "burning"
    job.subtitle_error = ""
    job.save(update_fields=["subtitle_status", "subtitle_error"])

    video_path = Path(out.file.path)
    if not video_path.exists():
        job.subtitle_status = "error"
        job.subtitle_error = "Arquivo não existe no disco."
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        return

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
            burn_subtitles(video_path, subs_path, output_tmp, job.subtitle_style)
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
    except Exception as e:
        job.subtitle_status = "error"
        job.subtitle_error = str(e)
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        raise
