"""Fluxo de análise de auto_cuts (refactor.md R-19 / D-11).

Este módulo é o destino do que era o corpo de `analyze_auto_cuts_task`, que tinha 652
linhas dentro de `apps/auto_cuts/tasks.py`. A task Celery continua lá — o nome dela é
contrato de fila e não pode mudar —, mas agora ela só delega.

Os fluxos de "cortes prontos" (`ready cuts`) moram aqui também: são ramos da análise,
chamados só por ela.

Nada aqui importa `tasks.py` no topo do módulo: a dependência é `tasks.py → services/`.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File

from apps.auto_cuts.services.flow_common import (
    _append_convidados,
    _queue_analysis_finalization,
    _resolve_target_brand_for_suggestion,
)
from apps.auto_cuts.services.grok import (
    analyze_ready_cut_metadata,
    analyze_ready_cuts_batch_titles_from_transcripts,
)
from apps.auto_cuts.services.transcript import segments_to_transcript_with_timestamps
from apps.jobs.services.ffmpeg import (
    concat_with_xfade,
    ffprobe_duration,
    has_nvenc,
    normalize_video_to_canvas,
    seconds_to_tc,
)
from apps.jobs.services.subtitles import generate_subtitles

logger = logging.getLogger(__name__)


def _process_ready_cuts_flow(analysis, duration_sec: float, segments: list) -> None:
    """
    Ready-cuts flow: video already edited.
    Transcribe, call LLM for metadata (title, thumbnail), copy video without re-extract,
    generate thumbnail, and finalize.
    """
    from apps.auto_cuts.models import AutoCutSuggestion

    analysis.status = "analyzing"
    analysis.progress_message = "Analisando metadata com IA..."
    analysis.progress = 20
    analysis.save(update_fields=["status", "progress_message", "progress"])

    transcript = analysis.transcript or ""
    tl = getattr(analysis, "ready_cuts_titles_language", None) or "pt"
    if tl not in ("pt", "en"):
        tl = "pt"
    metadata = analyze_ready_cut_metadata(transcript, duration_sec, titles_language=tl)
    title = _append_convidados(metadata.get("title") or "Vídeo", analysis.convidados)
    # LLM returns 1–10; normalize to 0–100 (scale used elsewhere)
    raw_score = metadata.get("virality_score") or 5
    virality_score = max(0, min(100, int(float(raw_score)) * 10)) if raw_score is not None else 50
    raw_data = {
        "thumbnail_moment_timestamp": metadata.get("thumbnail_moment_timestamp") or "00:00",
        "thumbnail_text": metadata.get("thumbnail_text") or "Vídeo",
    }

    analysis.progress_message = "Criando corte e thumbnail..."
    analysis.progress = 70
    analysis.save(update_fields=["progress_message", "progress"])

    video_path = Path(analysis.video_file.path)
    media_root = Path(settings.MEDIA_ROOT)
    cortes_dir = media_root / "auto_cuts" / "cortes"
    cortes_dir.mkdir(parents=True, exist_ok=True)

    AutoCutSuggestion.objects.filter(analysis=analysis).delete()
    AutoCutCorte = __import__("apps.auto_cuts.models", fromlist=["AutoCutCorte"]).AutoCutCorte
    from apps.auto_cuts.services.thumbnail import generate_auto_thumbnail

    end_tc = seconds_to_tc(duration_sec)
    sug = AutoCutSuggestion.objects.create(
        analysis=analysis,
        cut_type="short",
        start_tc="00:00",
        end_tc=end_tc,
        title=title,
        reason="",
        hook="",
        virality_score=virality_score,
        theme_category="",
        source_asset_id=f"analysis:{analysis.id}",
        rank=1,
        duration_seconds=duration_sec,
        raw_data=raw_data,
    )

    out_path = cortes_dir / f"job_{analysis.id}_sug_{sug.id}.mp4"
    shutil.copy(video_path, out_path)

    transcript_segments = analysis.transcript_segments or []
    subtitle_segments = [
        {"start": s.get("start", 0), "end": s.get("end", 0), "text": (s.get("text") or "").strip()}
        for s in transcript_segments
        if (s.get("text") or "").strip()
    ]

    corte = AutoCutCorte.objects.create(
        analysis=analysis,
        suggestion=sug,
        format="vertical",
        needs_subtitle=True,
        user_wants_finalize=True,
        is_finalized=False,
        subtitle_segments=subtitle_segments,
    )
    with open(out_path, "rb") as f:
        corte.file.save(out_path.name, File(f), save=True)

    target_brand = _resolve_target_brand_for_suggestion(analysis, sug)
    generate_auto_thumbnail(corte, target_brand=target_brand)

    _queue_analysis_finalization(analysis)
    logger.info("[FLUXO] Ready cuts flow completed successfully.")


def _merge_subtitle_segments_for_xfade(
    chunk_durations: list[float],
    fade_duration: float,
    segments_per_chunk: list[list[dict]],
) -> list[dict]:
    """Adjust segment timestamps to merged long video with xfade (same as concat_with_xfade)."""
    out = []
    fade = float(fade_duration)
    for i, segs in enumerate(segments_per_chunk):
        offset = sum(chunk_durations[j] for j in range(i)) - i * fade
        for s in segs or []:
            txt = (s.get("text") or "").strip()
            if not txt:
                continue
            st = offset + float(s.get("start", 0))
            en = offset + float(s.get("end", 0))
            if en < st:
                st, en = en, st
            out.append({"start": max(0.0, st), "end": max(0.0, en), "text": txt})
    out.sort(key=lambda x: x["start"])
    return out


def _base_name_for_ready_cuts_no_transcript(chunks: list, analysis) -> str:
    """Base name without transcript: job name (required on upload); else first file stem; else Job #id."""
    job = (getattr(analysis, "name", None) or "").strip()
    if job:
        return job
    first = chunks[0] if chunks else None
    base = ""
    if first and getattr(first, "file", None) and getattr(first.file, "name", None):
        base = Path(first.file.name).stem
    base = (base or "").strip()
    if not base:
        base = f"Job {getattr(analysis, 'id', '')}"
    return " ".join(base.replace("_", " ").split())


def _titles_for_ready_cuts_no_transcript(chunks: list, analysis) -> dict[str, str]:
    """Without transcript: '{job name} Part 1', 'Part 2', ... (no LLM)."""
    base = _base_name_for_ready_cuts_no_transcript(chunks, analysis)
    return {str(i): f"{base} Part {i + 1}"[:200] for i in range(len(chunks))}


def _process_ready_cuts_batch_flow(analysis_id: int) -> None:
    """
    Multiple files in one job: queued transcription, titles (LLM), optional long video (fade),
    then shorts; automatic finalization.
    """
    from apps.auto_cuts.models import (
        AutoCutAnalysis,
        AutoCutCorte,
        AutoCutReadyChunk,
        AutoCutSuggestion,
    )
    from apps.auto_cuts.services.thumbnail import generate_auto_thumbnail

    analysis = AutoCutAnalysis.objects.filter(id=analysis_id).first()
    if not analysis:
        return

    chunks_qs = AutoCutReadyChunk.objects.filter(analysis=analysis).order_by("order_index", "id")
    chunks = list(chunks_qs)
    if not chunks:
        analysis.status = "error"
        analysis.error = "Nenhum arquivo no lote de cortes prontos."
        analysis.save(update_fields=["status", "error"])
        return

    transcribe = bool(getattr(analysis, "ready_cuts_transcribe", True))
    fade_d = float(getattr(analysis, "ready_cuts_long_fade_duration", None) or 0.5)
    fade_d = max(0.1, min(3.0, fade_d))
    create_long = bool(getattr(analysis, "ready_cuts_create_long_video", False))
    titles_lang = getattr(analysis, "ready_cuts_titles_language", None) or "pt"
    if titles_lang not in ("pt", "en"):
        titles_lang = "pt"
    pv = (analysis.prompt_version or "viral").strip().lower()
    transcript_lang = "en" if pv in ("viral_en", "viral_long_en", "educational_en", "viral_translate") else "pt"

    analysis.status = "transcribing" if transcribe else "analyzing"
    analysis.progress_message = "Transcrevendo cortes..." if transcribe else "Medindo vídeos e gerando títulos..."
    analysis.progress = 8
    analysis.error = ""
    analysis.save(update_fields=["status", "progress_message", "progress", "error"])

    media_root = Path(settings.MEDIA_ROOT)
    cortes_dir = media_root / "auto_cuts" / "cortes"
    cortes_dir.mkdir(parents=True, exist_ok=True)

    for i, ch in enumerate(chunks):
        vp = Path(ch.file.path)
        if not vp.exists():
            analysis.status = "error"
            analysis.error = f"Arquivo ausente no chunk {i + 1}."
            analysis.save(update_fields=["status", "error"])
            return
        dur = ffprobe_duration(vp)
        ch.duration_seconds = dur
        to_update = ["duration_seconds"]
        if transcribe:
            analysis.progress_message = f"Transcrevendo vídeo {i + 1}/{len(chunks)}..."
            analysis.progress = 8 + int(35 * (i + 1) / max(len(chunks), 1))
            analysis.save(update_fields=["progress_message", "progress"])
            segs = generate_subtitles(vp, language=transcript_lang)
            if not segs:
                segs = []
            ch.transcript_segments = segs
            ch.transcript = segments_to_transcript_with_timestamps(segs) if segs else ""
            to_update += ["transcript_segments", "transcript"]
        ch.save(update_fields=to_update)

    analysis.progress_message = (
        "Gerando títulos com IA..."
        if transcribe
        else "Definindo títulos (nome do job + Part 1, 2, ...)..."
    )
    analysis.progress = 48
    analysis.status = "analyzing"
    analysis.save(update_fields=["progress_message", "progress", "status"])

    title_by_index: dict[str, str] = {}
    if transcribe:
        items = []
        for i, ch in enumerate(chunks):
            items.append({"id": str(i), "transcript": (ch.transcript or "")[:14000]})
        title_by_index = analyze_ready_cuts_batch_titles_from_transcripts(
            items, titles_language=titles_lang
        )
    else:
        title_by_index = _titles_for_ready_cuts_no_transcript(chunks, analysis)

    for i in range(len(chunks)):
        if str(i) in title_by_index and (title_by_index[str(i)] or "").strip():
            continue
        ch = chunks[i]
        if transcribe and (ch.transcript or "").strip():
            md = analyze_ready_cut_metadata(
                ch.transcript or "",
                float(ch.duration_seconds or 0),
                titles_language=titles_lang,
            )
            title_by_index[str(i)] = (md.get("title") or f"Vídeo {i + 1}")[:200]
        else:
            base = _base_name_for_ready_cuts_no_transcript(chunks, analysis)
            title_by_index[str(i)] = f"{base} Part {i + 1}"[:200]

    AutoCutSuggestion.objects.filter(analysis=analysis).delete()

    rank_counter = 1

    def _thumb_text_from_title(title: str) -> str:
        words = (title or "").replace("\n", " ").split()
        return (" ".join(words[:4]).upper()[:28] or "DESTAQUE")

    if create_long:
        analysis.progress_message = "Montando vídeo longo..."
        analysis.progress = 55
        analysis.save(update_fields=["progress_message", "progress"])
        parts = [Path(ch.file.path) for ch in chunks]
        long_path = cortes_dir / f"job_{analysis.id}_long_concat.mp4"
        try:
            # Normalize each clip to 1920×1080 (16:9) with letterbox/pillarbox for xfade
            # to accept mixed H/V and resolutions.
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)
                normalized_paths: list[Path] = []
                _gpu = has_nvenc()
                for i, p in enumerate(parts):
                    np = tmpdir_path / f"norm_{i}.mp4"
                    # Unified FPS/SAR/audio: xfade needs matching timebase between clips (e.g. 60 vs 25 fps).
                    normalize_video_to_canvas(
                        p, np, use_gpu=_gpu, target_fps=30, audio_hz=48000
                    )
                    normalized_paths.append(np)
                if len(normalized_paths) == 1:
                    shutil.copy(normalized_paths[0], long_path)
                else:
                    tmp_out = tmpdir_path / "long.mp4"
                    concat_with_xfade(normalized_paths, tmp_out, "fade", fade_d, _gpu)
                    shutil.copy(tmp_out, long_path)
        except Exception as e:
            logger.exception("[FLUXO] Failed to assemble long video: %s", e)
            analysis.status = "error"
            analysis.error = f"Erro ao montar vídeo longo: {e}"
            analysis.save(update_fields=["status", "error"])
            return

        long_dur = ffprobe_duration(long_path)
        merged_subs = []
        if transcribe:
            durs = [float(ch.duration_seconds or 0) for ch in chunks]
            segs_list = [ch.transcript_segments or [] for ch in chunks]
            merged_subs = _merge_subtitle_segments_for_xfade(durs, fade_d, segs_list)

        job_title = (analysis.name or "").strip() or "Vídeo longo"
        long_raw = {
            "thumbnail_moment_timestamp": "00:02",
            "thumbnail_text": _thumb_text_from_title(job_title),
        }
        long_sug = AutoCutSuggestion.objects.create(
            analysis=analysis,
            cut_type="long",
            start_tc="00:00",
            end_tc=seconds_to_tc(long_dur),
            title=job_title[:200],
            reason="",
            hook="",
            virality_score=70,
            theme_category="",
            source_asset_id=f"analysis:{analysis.id}:long",
            rank=rank_counter,
            duration_seconds=long_dur,
            duration_minutes=long_dur / 60.0,
            raw_data=long_raw,
        )
        rank_counter += 1
        long_corte = AutoCutCorte.objects.create(
            analysis=analysis,
            suggestion=long_sug,
            format="horizontal",
            needs_subtitle=transcribe,
            user_wants_finalize=True,
            is_finalized=False,
            subtitle_segments=merged_subs if transcribe else [],
        )
        with open(long_path, "rb") as f:
            long_corte.file.save(long_path.name, File(f), save=True)
        tb = _resolve_target_brand_for_suggestion(analysis, long_sug)
        generate_auto_thumbnail(long_corte, target_brand=tb)

    analysis.progress_message = "Criando cortes (shorts)..."
    analysis.progress = 72
    analysis.save(update_fields=["progress_message", "progress"])

    for i, ch in enumerate(chunks):
        title = _append_convidados(
            (title_by_index.get(str(i)) or "").strip() or f"Vídeo {i + 1}",
            analysis.convidados,
        )
        dsec = float(ch.duration_seconds or ffprobe_duration(Path(ch.file.path)))
        end_tc = seconds_to_tc(dsec)
        thumb_ts = min(4.5, max(0.5, min(dsec * 0.25, 5.0)))
        short_raw = {
            "thumbnail_moment_timestamp": "00:02",
            "thumbnail_text": _thumb_text_from_title(title),
            "thumbnail_frame_sec": thumb_ts,
        }
        sug = AutoCutSuggestion.objects.create(
            analysis=analysis,
            cut_type="short",
            start_tc="00:00",
            end_tc=end_tc,
            title=title[:200],
            reason="",
            hook="",
            virality_score=75,
            theme_category="",
            source_asset_id=f"ready_chunk:{ch.id}",
            rank=rank_counter,
            duration_seconds=dsec,
            raw_data=short_raw,
        )
        rank_counter += 1
        sub_seg = []
        if transcribe and ch.transcript_segments:
            sub_seg = [
                {"start": s.get("start", 0), "end": s.get("end", 0), "text": (s.get("text") or "").strip()}
                for s in ch.transcript_segments
                if (s.get("text") or "").strip()
            ]
        out_path = cortes_dir / f"job_{analysis.id}_sug_{sug.id}.mp4"
        shutil.copy(Path(ch.file.path), out_path)
        corte = AutoCutCorte.objects.create(
            analysis=analysis,
            suggestion=sug,
            format="vertical",
            needs_subtitle=transcribe,
            user_wants_finalize=True,
            is_finalized=False,
            subtitle_segments=sub_seg,
        )
        with open(out_path, "rb") as f:
            corte.file.save(out_path.name, File(f), save=True)
        tb = _resolve_target_brand_for_suggestion(analysis, sug)
        generate_auto_thumbnail(corte, target_brand=tb)

    _queue_analysis_finalization(analysis)
    logger.info("[FLUXO] Ready cuts batch completed (analysis=%s, long=%s).", analysis_id, bool(create_long))
