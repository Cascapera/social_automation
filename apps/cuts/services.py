"""Extrai cortes do vídeo source e salva como arquivos. Deleta o source após."""

from pathlib import Path

from django.conf import settings

from apps.mediahub.models import SourceVideo
from apps.jobs.services.ffmpeg import cut_clip, make_vertical_blur, has_nvenc, tc_to_seconds


def extract_cuts_from_source(source_id: int, cuts_data: list) -> list:
    """
    Extrai cada corte do source, salva em cuts/, cria Cut com file.
    Deleta o source file ao final.
    Retorna lista de Cut criados.
    """
    from apps.cuts.models import Cut

    source = SourceVideo.objects.get(id=source_id)
    source_file = Path(source.file.path)
    if not source_file.exists():
        raise FileNotFoundError(f"Arquivo do source não encontrado: {source_file}")

    use_gpu = has_nvenc()
    media_root = Path(settings.MEDIA_ROOT)
    cuts_dir = media_root / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)

    created = []
    for idx, c in enumerate(cuts_data):
        name = c.get("name", "")
        start_tc = c["start_tc"]
        end_tc = c["end_tc"]
        is_vertical = c.get("format", "vertical") == "vertical"

        raw_path = cuts_dir / f"source_{source_id}_cut_{idx}.mp4"
        cut_clip(source_file, start_tc, end_tc, raw_path, use_gpu=use_gpu)

        if is_vertical:
            final_path = cuts_dir / f"source_{source_id}_cut_{idx}_9x16.mp4"
            make_vertical_blur(raw_path, final_path, use_gpu=use_gpu)
            raw_path.unlink(missing_ok=True)
        else:
            final_path = raw_path

        rel_path = f"cuts/{final_path.name}"
        duration_sec = tc_to_seconds(end_tc) - tc_to_seconds(start_tc)
        if duration_sec < 0:
            duration_sec = 0
        cut = Cut.objects.create(
            source=source,
            brand=source.brand,
            user=source.user,
            name=name,
            start_tc=start_tc,
            end_tc=end_tc,
            format="vertical" if is_vertical else "horizontal",
            duration=duration_sec,
            file=rel_path,
        )
        created.append(cut)

    source.file.delete(save=True)
    Cut.objects.filter(id__in=[c.id for c in created]).update(source=None)
    source.delete()
    return created
