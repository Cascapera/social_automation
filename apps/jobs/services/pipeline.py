from pathlib import Path
import shutil

from django.conf import settings
from django.utils import timezone

from apps.jobs.models import Job, RenderOutput
from .paths import get_job_paths
from .ffmpeg import (
    cut_clip,
    make_vertical_blur,
    normalize_part_for_concat,
    concat_videos_copy,
    concat_with_xfade,
    has_nvenc,
)

def _append_log(job: Job, msg: str) -> None:
    job.log = (job.log or "") + msg.rstrip() + "\n"
    job.save(update_fields=["log"])

def _set_progress(job: Job, value: int) -> None:
    job.progress = max(0, min(100, value))
    job.save(update_fields=["progress"])

def run_job(job_id: int) -> None:
    job = Job.objects.select_related(
        "intro_asset", "outro_asset"
    ).prefetch_related("job_cuts__cut__source").get(id=job_id)

    use_gpu = has_nvenc()

    job_cuts = list(job.job_cuts.select_related("cut", "cut__source").order_by("id"))
    if not job_cuts:
        raise ValueError("Job precisa de pelo menos 1 corte")

    job.status = "RUNNING"
    job.started_at = timezone.now()
    job.error = ""
    job.save(update_fields=["status", "started_at", "error"])

    paths = get_job_paths(job.id)

    try:
        _append_log(job, f"make_vertical={job.make_vertical!r} (from DB)")
        _append_log(job, f"GPU NVENC: {'ON' if use_gpu else 'OFF (CPU)'}")

        cut_paths = []
        total_cuts = len(job_cuts)
        for idx, jc in enumerate(job_cuts):
            cut = jc.cut
            source_file = Path(cut.source.file.path)
            _append_log(job, f"[1/4] Cutting {idx+1}/{total_cuts}: {cut.start_tc} -> {cut.end_tc}")
            _set_progress(job, 10 + int(25 * idx / max(1, total_cuts)))
            cut_path = paths.workspace / f"cut_{cut.id}_{idx}.mp4"
            cut_clip(source_file, cut.start_tc, cut.end_tc, cut_path, use_gpu=use_gpu)
            cut_paths.append(cut_path)
        _set_progress(job, 35)

        main_paths = []
        for idx, cut_path in enumerate(cut_paths):
            if job.make_vertical:
                vertical_path = paths.workspace / f"cut_{job_cuts[idx].cut.id}_{idx}_9x16.mp4"
                make_vertical_blur(cut_path, vertical_path, use_gpu=use_gpu)
                main_paths.append(vertical_path)
            else:
                main_paths.append(cut_path)
        if job.make_vertical:
            _append_log(job, "[2/4] Vertical 9:16 (blur bg)")
        _set_progress(job, 60)

        parts = []
        if job.intro_asset:
            parts.append(Path(job.intro_asset.file.path))
        parts.extend(main_paths)
        if job.outro_asset:
            parts.append(Path(job.outro_asset.file.path))

        _append_log(job, f"Output format: {'vertical 9:16' if job.make_vertical else 'horizontal 16:9'}")
        _append_log(job, f"[3/4] Normalize + concat parts={len(parts)}")
        _set_progress(job, 70)
        normalized = []
        for i, p in enumerate(parts):
            norm_path = paths.workspace / f"part_{i}.mp4"
            normalize_part_for_concat(
                p, norm_path, use_gpu=use_gpu, make_vertical=job.make_vertical
            )
            normalized.append(norm_path)
        _set_progress(job, 85)
        export_tmp = paths.exports / f"job_{job.id}.mp4"
        use_transition = (
            job.transition
            and job.transition != "none"
            and len(normalized) >= 2
        )
        if use_transition:
            dur = float(job.transition_duration or 0.5)
            _append_log(job, f"Concat with xfade: {job.transition} ({dur}s)")
            concat_with_xfade(
                normalized,
                export_tmp,
                transition=job.transition,
                duration_sec=dur,
                use_gpu=use_gpu,
            )
        else:
            concat_videos_copy(normalized, export_tmp, paths.workspace)

        _append_log(job, "[4/4] Save output")
        _set_progress(job, 95)

        media_root = Path(settings.MEDIA_ROOT)
        final_dir = media_root / "exports"
        final_dir.mkdir(parents=True, exist_ok=True)

        final_path = final_dir / export_tmp.name
        shutil.copy2(export_tmp, final_path)

        out = RenderOutput.objects.filter(job=job).first() or RenderOutput(job=job)
        out.file.name = f"exports/{final_path.name}"
        out.save()

        # Remove arquivos temporários (workspace, exports) da pasta do job
        job_dir = (Path(settings.MEDIA_ROOT) / "jobs" / str(job.id)).resolve()
        try:
            if job_dir.exists():
                shutil.rmtree(job_dir)
                _append_log(job, "[4/4] Temp files removed")
        except OSError as e:
            _append_log(job, f"[WARN] Could not remove temp dir: {e}")

        _set_progress(job, 100)
        job.status = "DONE"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
        _append_log(job, f"[DONE] {out.file.name}")

    except Exception as e:
        job.status = "FAILED"
        job.error = str(e)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        _append_log(job, f"[ERROR] {e}")
        raise
