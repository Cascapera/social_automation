"""Tasks Celery para análise de cortes automáticos."""

import logging
import os
import tempfile
from pathlib import Path

from celery import shared_task

logger = logging.getLogger(__name__)
from django.conf import settings
from django.core.files import File
from django.core.exceptions import ObjectDoesNotExist
from django.db.utils import DatabaseError

from apps.jobs.services.ffmpeg import ffprobe_duration
from apps.jobs.services.subtitles import generate_subtitles, segments_to_srt, burn_subtitles
from apps.auto_cuts.services.transcript import (
    chunk_transcript,
    segments_to_transcript_with_timestamps,
)
from apps.auto_cuts.services.video_chunks import (
    extract_chunks_to_folder,
    transcribe_single_chunk,
    cleanup_cortes_processo,
    get_chunk_boundaries,
)
from apps.auto_cuts.services.grok import analyze_chunks_in_one_request

# Vídeos maiores que isso usam transcrição por chunks (evita crash de memória)
CHUNKED_TRANSCRIPTION_THRESHOLD_SEC = 10 * 60  # 10 min
VIRAL_SHORT_MIN_SEC = 30
VIRAL_SHORT_MAX_SEC = 60
VIRAL_LONG_MIN_SEC = 8 * 60
VIRAL_LONG_MAX_SEC = 15 * 60
EDUCATIONAL_SHORT_MAX_SEC = 180


def _pick_timestamp(item: dict, start: bool = True) -> str:
    """Aceita chaves antigas e novas de timestamp."""
    if start:
        return item.get("start") or item.get("start_timestamp") or ""
    return item.get("end") or item.get("end_timestamp") or ""


def _normalize_virality_score(value) -> int | None:
    """Converte score em inteiro 0..100 (aceita '96%' ou número)."""
    if value is None:
        return None
    try:
        score = int(float(str(value).replace("%", "").strip()))
    except Exception:
        return None
    return max(0, min(100, score))


def _sort_by_virality(items: list[dict]) -> list[dict]:
    """Ordena itens por score viral desc, com fallback para rank asc."""
    return sorted(
        items,
        key=lambda item: (
            -(_normalize_virality_score(item.get("virality_score")) or -1),
            int(item.get("rank") or 9999),
        ),
    )


def _safe_save_analysis(analysis, update_fields):
    """
    Salva analysis; retorna False se o registro foi deletado (caller deve retornar).
    Evita DatabaseError quando o job é deletado durante o processamento.
    """
    try:
        analysis.save(update_fields=update_fields)
        return True
    except DatabaseError as e:
        if "did not affect any rows" in str(e):
            return False
        raise


@shared_task(bind=True)
def analyze_auto_cuts_task(self, analysis_id: int) -> None:
    """Transcreve, analisa em chunks e agrega sugestões de cortes virais."""
    from apps.auto_cuts.models import AutoCutAnalysis, AutoCutSuggestion

    try:
        analysis = AutoCutAnalysis.objects.get(id=analysis_id)
    except ObjectDoesNotExist:
        return  # Análise deletada; ignora task da fila

    # Idempotência: se já concluído, retorna sem reprocessar (evita loop quando Celery
    # trava ao liberar GPU e a task é reentregue após reinício)
    if analysis.status == "done":
        logger.info("[FLUXO] Analysis %s já concluída; ignorando reexecução.", analysis_id)
        return

    youtube_url = (analysis.youtube_url or "").strip()
    pv = (analysis.prompt_version or "viral").strip().lower()
    transcript_lang = "en" if pv in ("viral_en", "educational_en") else "pt"
    analysis.status = "transcribing"
    analysis.progress_message = "Baixando vídeo do YouTube..." if youtube_url else "Transcrevendo vídeo..."
    analysis.progress = 2 if youtube_url else 5
    analysis.error = ""
    analysis.save(update_fields=["status", "progress_message", "progress", "error"])

    # Se tem URL do YouTube, baixar primeiro
    if youtube_url:
        analysis.progress_message = "Baixando vídeo do YouTube..."
        analysis.progress = 2
        if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
            return
        try:
            from apps.auto_cuts.services.youtube_download import download_youtube
            media_root = Path(settings.MEDIA_ROOT)
            download_dir = media_root / "auto_cuts" / "sources"
            download_dir.mkdir(parents=True, exist_ok=True)
            out_path = download_dir / f"yt_{analysis_id}.mp4"
            downloaded = download_youtube(youtube_url, out_path)
            with open(downloaded, "rb") as f:
                analysis.file.save(downloaded.name, File(f), save=True)
            # Mantém youtube_url para metadados de publicação (descrição/episódio completo).
            analysis.save(update_fields=["file"])
            # Remove arquivo original do yt-dlp se em outro path (ex: video_id.mp4)
            saved_path = Path(analysis.file.path)
            if downloaded.resolve() != saved_path.resolve() and downloaded.exists():
                try:
                    downloaded.unlink()
                except Exception:
                    pass
            logger.info("[FLUXO] YouTube baixado: %s", analysis.file.name)
        except Exception as e:
            logger.exception("[FLUXO] Erro ao baixar YouTube: %s", e)
            analysis.status = "error"
            analysis.error = f"Erro ao baixar vídeo: {e}"
            analysis.save(update_fields=["status", "error"])
            return

    # Obter caminho do vídeo
    video_file = analysis.video_file
    if not video_file:
        analysis.status = "error"
        analysis.error = "Nenhum vídeo encontrado (source ou upload)."
        analysis.save(update_fields=["status", "error"])
        return

    video_path = Path(video_file.path)
    if not video_path.exists():
        analysis.status = "error"
        analysis.error = "Arquivo de vídeo não existe no disco."
        analysis.save(update_fields=["status", "error"])
        return

    try:
        duration_sec = ffprobe_duration(video_path)
        use_chunked = duration_sec > CHUNKED_TRANSCRIPTION_THRESHOLD_SEC

        if use_chunked:
            # Fluxo: extrai chunks → salva em cortes_processo → transcreve 1 por 1 → deleta chunk
            # Cada chunk = 18 min (vídeos pequenos no disco, sem temp em memória)
            try:
                analysis.progress_message = "Extraindo blocos de áudio..."
                if not _safe_save_analysis(analysis, ["progress_message"]):
                    return
                chunk_paths = extract_chunks_to_folder(
                    video_path, analysis.id,
                    chunk_minutes=18, overlap_minutes=3)
                total_chunks = len(chunk_paths)
                all_segments = []
                boundaries = [(s, e) for _, s, e in chunk_paths]

                # Loop simples (sem gerador) - evita crash ao sair do gerador em vídeos longos
                from apps.jobs.services.subtitles import load_whisper_model
                _whisper_model, _ = load_whisper_model(model_size=os.getenv("WHISPER_MODEL", "small").strip() or "small", device=None)

                for i, (chunk_path, start_sec, end_sec) in enumerate(chunk_paths):
                    analysis.progress_message = f"Transcrevendo bloco {i + 1}/{total_chunks}..."
                    analysis.progress = 5 + int(15 * (i + 1) / total_chunks)
                    if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
                        logger.info("[FLUXO] Analysis %s deletada durante transcrição; abortando.", analysis_id)
                        return
                    chunk = transcribe_single_chunk(_whisper_model, chunk_path, start_sec, end_sec, language=transcript_lang)
                    prev_end = boundaries[i - 1][1] if i > 0 else 0
                    segs_to_add = [
                        {"start": s.get("start"), "end": s.get("end"), "text": s.get("text", "").strip()}
                        for s in chunk["segments"]
                        if s.get("start", 0) >= prev_end
                    ]
                    all_segments.extend(segs_to_add)
                    chunk["segments"] = []  # Libera memória
                    logger.info("[FLUXO] Chunk %d/%d: OK (total %d segmentos)", i + 1, total_chunks, len(all_segments))

                # Não fazer del/gc aqui - liberação explícita da GPU pode causar crash no Windows
                logger.info("[FLUXO] Loop transcrição OK. %d segmentos. Ordenando...", len(all_segments))
                all_segments.sort(key=lambda s: s.get("start", 0))
                logger.info("[FLUXO] Ordenado. Montando transcript string...")
                analysis.transcript_segments = all_segments
                analysis.transcript = segments_to_transcript_with_timestamps(all_segments)
                logger.info("[FLUXO] Transcript montado (%d chars).", len(analysis.transcript or ""))
            finally:
                logger.info("[FLUXO] Iniciando cleanup cortes_processo...")
                cleanup_cortes_processo(analysis.id)
                logger.info("[FLUXO] Transcrição por chunks concluída. Cleanup feito.")
        else:
            # Fluxo original: transcreve vídeo inteiro
            segments = generate_subtitles(video_path, language=transcript_lang)
            if not segments:
                analysis.status = "error"
                analysis.error = "Nenhum segmento transcrito."
                analysis.save(update_fields=["status", "error"])
                return

            analysis.transcript_segments = segments
            analysis.transcript = segments_to_transcript_with_timestamps(segments)
            logger.info("[FLUXO] Transcrição única concluída.")

        segments = analysis.transcript_segments or []
        if not segments:
            analysis.status = "error"
            analysis.error = "Transcrição vazia."
            analysis.save(update_fields=["status", "error"])
            return

        logger.info("[FLUXO] Iniciando chunk_transcript (%d segmentos)...", len(segments))
        chunks = chunk_transcript(segments, chunk_minutes=18, overlap_minutes=3)
        logger.info("[FLUXO] chunk_transcript concluído: %d blocos.", len(chunks) if chunks else 0)
        if not chunks:
            analysis.status = "error"
            analysis.error = "Não foi possível dividir a transcrição em blocos."
            analysis.save(update_fields=["status", "error"])
            return

        analysis.status = "analyzing"
        analysis.progress_message = f"Analisando {len(chunks)} blocos com IA (1 requisição)..."
        analysis.progress = 20
        analysis.save(update_fields=["transcript_segments", "transcript", "status", "progress_message", "progress"])

        logger.info("[FLUXO] Chamando Grok API (analyze_chunks_in_one_request)... %d blocos", len(chunks))
        MAX_RETRIES = 3
        final = None
        for attempt in range(MAX_RETRIES):
            try:
                final = analyze_chunks_in_one_request(
                    chunks,
                    assunto=analysis.assunto or "",
                    convidados=analysis.convidados or "",
                    prompt_version=analysis.prompt_version or "viral",
                )
                logger.info(
                    "[FLUXO] Grok API respondeu OK. candidates=%d, ranked_shorts=%d, final_long_cuts=%d",
                    len(final.get("candidate_shorts", [])),
                    len(final.get("ranked_shorts", [])),
                    len(final.get("final_long_cuts", [])),
                )
                break
            except Exception as e:
                logger.warning("[FLUXO] Grok API falhou (tentativa %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                if attempt < MAX_RETRIES - 1:
                    analysis.progress_message = (
                        f"Análise falhou (tentativa {attempt + 1}/{MAX_RETRIES}), repetindo..."
                    )
                    if not _safe_save_analysis(analysis, ["progress_message"]):
                        return
                else:
                    logger.exception("[FLUXO] Grok API falhou após %d tentativas", MAX_RETRIES)
                    analysis.status = "error"
                    analysis.error = "Falha na análise após 3 tentativas."
                    analysis.save(update_fields=["status", "error"])
                    return

        logger.info("[FLUXO] Salvando sugestões e extraindo cortes...")
        analysis.progress_message = "Extraindo cortes..."
        analysis.progress = 85
        if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
            return

        # Salvar sugestões e extrair cortes
        AutoCutSuggestion.objects.filter(analysis=analysis).delete()
        AutoCutCorte = __import__("apps.auto_cuts.models", fromlist=["AutoCutCorte"]).AutoCutCorte
        from apps.auto_cuts.services.extract import extract_corte
        from apps.auto_cuts.services.thumbnail import generate_auto_thumbnail

        video_path = Path(analysis.video_file.path)
        media_root = Path(settings.MEDIA_ROOT)
        cortes_dir = media_root / "auto_cuts" / "cortes"
        cortes_dir.mkdir(parents=True, exist_ok=True)

        suggestions_created = []
        rank = 0
        is_viral_prompt = pv in ("viral", "viral_en")
        is_educational_prompt = pv in ("educational", "educational_en")
        shorts_limit = max(1, min(30, int(getattr(analysis, "shorts_target", 12) or 12)))
        longs_limit = max(1, min(10, int(getattr(analysis, "longs_target", 3) or 3)))
        from apps.jobs.services.ffmpeg import tc_to_seconds, seconds_to_tc

        ranked_shorts_source = final.get("ranked_shorts") or final.get("candidate_shorts") or []
        ranked_shorts = _sort_by_virality(ranked_shorts_source)[:shorts_limit]
        for item in ranked_shorts:
            start_tc = _pick_timestamp(item, start=True)
            end_tc = _pick_timestamp(item, start=False)
            duration_seconds = item.get("duration") or item.get("duration_seconds")

            start_sec = tc_to_seconds(start_tc)
            end_sec = tc_to_seconds(end_tc)
            if end_sec <= start_sec:
                logger.info(
                    "[FLUXO] Short inválido ignorado (end<=start): %s -> %s",
                    start_tc,
                    end_tc,
                )
                continue

            if is_viral_prompt:
                raw_duration = end_sec - start_sec
                if raw_duration < VIRAL_SHORT_MIN_SEC:
                    logger.info(
                        "[FLUXO] Short viral ignorado por duração < %ss: %.2fs (%s -> %s)",
                        VIRAL_SHORT_MIN_SEC,
                        raw_duration,
                        start_tc,
                        end_tc,
                    )
                    continue
                if raw_duration > VIRAL_SHORT_MAX_SEC:
                    end_sec = start_sec + VIRAL_SHORT_MAX_SEC
                    end_tc = seconds_to_tc(end_sec)
                    raw_duration = VIRAL_SHORT_MAX_SEC
                duration_seconds = raw_duration
            elif is_educational_prompt:
                raw_duration = end_sec - start_sec
                if raw_duration > EDUCATIONAL_SHORT_MAX_SEC:
                    end_sec = start_sec + EDUCATIONAL_SHORT_MAX_SEC
                    end_tc = seconds_to_tc(end_sec)
                    raw_duration = EDUCATIONAL_SHORT_MAX_SEC
                duration_seconds = raw_duration

            rank += 1
            sug = AutoCutSuggestion.objects.create(
                analysis=analysis,
                cut_type="short",
                start_tc=start_tc,
                end_tc=end_tc,
                title=item.get("title") or item.get("suggested_title", ""),
                reason=item.get("reason") or item.get("main_topic", ""),
                hook=item.get("hook") or item.get("hook_sentence", ""),
                virality_score=_normalize_virality_score(item.get("virality_score")),
                rank=rank,
                duration_seconds=duration_seconds,
                raw_data=item,
            )
            suggestions_created.append((sug, "vertical"))

        ranked_longs = _sort_by_virality(final.get("final_long_cuts") or [])[:longs_limit]
        for item in ranked_longs:
            start_tc = _pick_timestamp(item, start=True)
            end_tc = _pick_timestamp(item, start=False)
            start_sec = tc_to_seconds(start_tc)
            end_sec = tc_to_seconds(end_tc)
            if end_sec <= start_sec:
                logger.info(
                    "[FLUXO] Long inválido ignorado (end<=start): %s -> %s",
                    start_tc,
                    end_tc,
                )
                continue
            if is_viral_prompt:
                raw_duration = end_sec - start_sec
                if raw_duration < VIRAL_LONG_MIN_SEC:
                    logger.info(
                        "[FLUXO] Long viral ignorado por duração < %ss: %.2fs (%s -> %s)",
                        VIRAL_LONG_MIN_SEC,
                        raw_duration,
                        start_tc,
                        end_tc,
                    )
                    continue
                if raw_duration > VIRAL_LONG_MAX_SEC:
                    end_sec = start_sec + VIRAL_LONG_MAX_SEC
                    end_tc = seconds_to_tc(end_sec)
                    raw_duration = VIRAL_LONG_MAX_SEC
                duration_minutes = round(raw_duration / 60.0, 2)
            else:
                duration_minutes = item.get("duration_min")

            sug = AutoCutSuggestion.objects.create(
                analysis=analysis,
                cut_type="long",
                start_tc=start_tc,
                end_tc=end_tc,
                title=item.get("title_suggestion") or item.get("suggested_title") or item.get("title", ""),
                reason=item.get("reason") or item.get("main_topic", ""),
                virality_score=_normalize_virality_score(item.get("virality_score")),
                duration_minutes=duration_minutes,
                raw_data=item,
            )
            suggestions_created.append((sug, "horizontal"))

        logger.info("[FLUXO] %d sugestões criadas. Iniciando extração de vídeos...", len(suggestions_created))
        # 6. Extrair vídeo de cada sugestão e criar AutoCutCorte
        total_cortes = len(suggestions_created)
        for i, (sug, fmt) in enumerate(suggestions_created):
            analysis.progress_message = f"Extraindo corte {i + 1}/{total_cortes}..."
            analysis.progress = 85 + int(10 * (i + 1) / total_cortes)
            if not _safe_save_analysis(analysis, ["progress_message", "progress"]):
                logger.info("[FLUXO] Analysis %s deletada durante extração; abortando.", analysis_id)
                return

            out_path = cortes_dir / f"job_{analysis.id}_sug_{sug.id}.mp4"
            try:
                logger.info("[FLUXO] Extraindo corte %d/%d: %s -> %s", i + 1, total_cortes, sug.start_tc, sug.end_tc)
                extract_corte(video_path, sug.start_tc, sug.end_tc, out_path, use_gpu=False)
            except Exception as e:
                analysis.status = "error"
                analysis.error = f"Erro ao extrair corte {i + 1}: {e}"
                analysis.save(update_fields=["status", "error"])
                return

            cut_start_sec = tc_to_seconds(sug.start_tc)
            cut_end_sec = tc_to_seconds(sug.end_tc)
            cut_duration = cut_end_sec - cut_start_sec
            transcript_segments = analysis.transcript_segments or []
            subtitle_segments = []
            for seg in transcript_segments:
                s_start = seg.get("start", 0)
                s_end = seg.get("end", 0)
                if s_end <= cut_start_sec or s_start >= cut_end_sec:
                    continue
                new_start = max(0.0, s_start - cut_start_sec)
                new_end = min(cut_duration, s_end - cut_start_sec)
                text = (seg.get("text") or "").strip()
                if text:
                    subtitle_segments.append({"start": new_start, "end": new_end, "text": text})

            # Shorts (vertical) têm legendas queimadas por padrão
            corte = AutoCutCorte.objects.create(
                analysis=analysis,
                suggestion=sug,
                format=fmt,
                needs_subtitle=(fmt == "vertical"),
                user_wants_finalize=False,
                is_finalized=False,
                subtitle_segments=subtitle_segments,
            )
            with open(out_path, "rb") as f:
                corte.file.save(out_path.name, File(f), save=True)
            generated_thumb = generate_auto_thumbnail(corte)
            if generated_thumb:
                logger.info("[FLUXO] Thumbnail automática gerada para corte %s.", corte.id)
            else:
                logger.info("[FLUXO] Thumbnail automática indisponível para corte %s.", corte.id)

        logger.info("[FLUXO] Todos os %d cortes extraídos. Salvando status final.", total_cortes)
        analysis.status = "done"
        analysis.progress_message = "Concluído"
        analysis.progress = 100
        if not _safe_save_analysis(analysis, ["status", "progress_message", "progress"]):
            logger.info("[FLUXO] Analysis %s deletada antes do save final; ignorando.", analysis_id)
        logger.info("[FLUXO] Task concluída com sucesso.")

    except Exception as e:
        if isinstance(e, DatabaseError) and "did not affect any rows" in str(e):
            logger.info("[FLUXO] Analysis %s deletada durante processamento; abortando.", analysis_id)
            return
        AutoCutAnalysis.objects.filter(id=analysis_id).update(status="error", error=str(e))
        raise


# Estilo padrão para legendas (fonte com emojis)
DEFAULT_SUBTITLE_STYLE = {
    "font": "Segoe UI Emoji",
    "size": 24,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline": 2,
}


@shared_task(bind=True)
def finalizar_auto_cut_task(
    self,
    analysis_id: int,
    subtitle_style: dict | None = None,
    vertical_mode: str | None = None,
    background_color: str | None = None,
    custom_text: str | None = None,
    font_size_title: int | None = None,
    font_size_text: int | None = None,
    title_color: str | None = None,
    text_color: str | None = None,
    horizontal_insert_logo: bool = False,
    horizontal_logo_x: int | None = None,
    horizontal_logo_y: int | None = None,
    overlay_animation_asset_id: int | None = None,
    overlay_position: str | None = None,
    overlay_margin: int | None = None,
    overlay_height: int | None = None,
) -> None:
    """
    Finaliza cortes: deleta os não marcados, reenquadra verticais (se 16:9),
    queima legendas nos marcados com needs_subtitle, marca todos como finalizados.
    """
    from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte
    from apps.brands.models import BrandAsset
    from apps.auto_cuts.services.vertical_reformat import reformat_video_vertical
    from apps.jobs.services.ffmpeg import ffprobe_video_info, overlay_logo, overlay_animation

    try:
        analysis = AutoCutAnalysis.objects.get(id=analysis_id)
    except ObjectDoesNotExist:
        return

    style = {**DEFAULT_SUBTITLE_STYLE, **(subtitle_style or {})}
    vert_mode = vertical_mode or "frame_center"
    bg_color = (background_color or "#000000").strip()
    link_text = (custom_text or "").strip()
    title_font = 36 if font_size_title is None else max(12, min(96, int(font_size_title)))
    text_font = 28 if font_size_text is None else max(12, min(72, int(font_size_text)))
    title_clr = (title_color or "#FFFFFF").strip()
    text_clr = (text_color or "#FFFFFF").strip()
    horiz_logo = bool(horizontal_insert_logo)
    horiz_logo_x = max(0, min(2000, int(horizontal_logo_x or 0)))
    horiz_logo_y = max(0, min(1200, int(horizontal_logo_y or 0)))
    overlay_pos = (overlay_position or "bottom_right").strip() or "bottom_right"
    overlay_m = max(0, min(100, int(overlay_margin or 24)))
    overlay_h = max(20, min(400, int(overlay_height or 120)))

    # Animação overlay (PNG/GIF com transparência)
    animation_path = None
    if overlay_animation_asset_id and analysis.brand_id:
        anim_asset = BrandAsset.objects.filter(
            id=overlay_animation_asset_id,
            brand_id=analysis.brand_id,
            asset_type="ANIMATION",
        ).first()
        if anim_asset and anim_asset.file:
            try:
                animation_path = Path(anim_asset.file.path)
            except Exception:
                animation_path = None

    # Logo da marca (para frame_center)
    logo_path = None
    if analysis.brand_id:
        logo_asset = BrandAsset.objects.filter(
            brand_id=analysis.brand_id, asset_type="LOGO"
        ).first()
        if logo_asset and logo_asset.file:
            try:
                logo_path = Path(logo_asset.file.path)
            except Exception:
                logo_path = None

    to_delete = list(AutoCutCorte.objects.filter(analysis=analysis, user_wants_finalize=False))
    media_root = Path(settings.MEDIA_ROOT)
    cortes_dir = media_root / "auto_cuts" / "cortes"
    to_delete_sug_ids = {c.suggestion_id for c in to_delete}

    for corte in to_delete:
        if corte.file:
            try:
                fp = Path(corte.file.path) if corte.file.name else None
            except Exception:
                fp = None
            try:
                corte.file.delete(save=False)
            except Exception:
                pass
            if fp and fp.exists():
                try:
                    fp.unlink()
                except Exception:
                    pass
        corte.delete()

    if cortes_dir.exists() and to_delete_sug_ids:
        try:
            for sug_id in to_delete_sug_ids:
                for f in cortes_dir.glob(f"job_{analysis.id}_sug_{sug_id}.mp4"):
                    if f.exists():
                        f.unlink()
        except Exception:
            pass

    to_finalize = list(AutoCutCorte.objects.filter(analysis=analysis, user_wants_finalize=True))

    for corte in to_finalize:
        if not corte.file:
            corte.is_finalized = True
            corte.save(update_fields=["is_finalized"])
            continue

        video_path = Path(corte.file.path)
        if not video_path.exists():
            corte.is_finalized = True
            corte.save(update_fields=["is_finalized"])
            continue

        try:
            work_path = video_path
            # 1. Reenquadrar vertical (shorts com vídeo fonte horizontal)
            info = ffprobe_video_info(video_path)
            w, h = info.get("width", 0), info.get("height", 0)
            is_horizontal = w > 0 and h > 0 and w > h
            needs_reformat = (
                corte.format == "vertical"
                and is_horizontal
                and vert_mode in ("frame_center", "zoom_crop")
            )
            if not needs_reformat and corte.format == "vertical":
                logger.info(
                    "Corte %s (vertical): pulando reenquadramento - dims=%dx%d, vert_mode=%s",
                    corte.id, info.get("width", 0), info.get("height", 0), vert_mode,
                )
            if needs_reformat:
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        reformat_out = Path(tmpdir) / "reformatted.mp4"
                        reformat_video_vertical(
                            video_path,
                            reformat_out,
                            vert_mode,
                            background_color=bg_color,
                            logo_path=logo_path if vert_mode == "frame_center" else None,
                            title=(corte.suggestion.title or "").strip() if vert_mode == "frame_center" else "",
                            custom_text=link_text if vert_mode == "frame_center" else "",
                            font_size_title=title_font,
                            font_size_text=text_font,
                            title_color=title_clr,
                            text_color=text_clr,
                            use_gpu=False,
                        )
                        corte.file.delete(save=False)
                        with open(reformat_out, "rb") as f:
                            corte.file.save(
                                f"job_{analysis.id}_sug_{corte.suggestion_id}_reformatted.mp4",
                                File(f),
                                save=True,
                            )
                        work_path = Path(corte.file.path)
                        logger.info("Corte %s: reenquadrado para vertical (%s)", corte.id, vert_mode)
                except Exception as e:
                    logger.exception("Erro ao reenquadrar vertical corte %s: %s", corte.id, e)

            # 2. Sobrepõe animação (cortes curtos e longos, se solicitado)
            if animation_path and animation_path.exists() and work_path.exists():
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        anim_out = Path(tmpdir) / "with_anim.mp4"
                        overlay_animation(
                            work_path,
                            anim_out,
                            animation_path,
                            position=overlay_pos,
                            margin=overlay_m,
                            height=overlay_h,
                            use_gpu=False,
                        )
                        corte.file.delete(save=False)
                        with open(anim_out, "rb") as f:
                            corte.file.save(
                                f"job_{analysis.id}_sug_{corte.suggestion_id}_anim.mp4",
                                File(f),
                                save=True,
                            )
                        work_path = Path(corte.file.path)
                        logger.info("Corte %s: animação overlay aplicada (%s)", corte.id, overlay_pos)
                except Exception as e:
                    logger.exception("Erro ao aplicar animação overlay corte %s: %s", corte.id, e)

            # 3. Inserir logo em cortes horizontais (se solicitado)
            if corte.format == "horizontal" and horiz_logo and logo_path and logo_path.exists():
                if work_path.exists():
                    try:
                        with tempfile.TemporaryDirectory() as tmpdir:
                            logo_out = Path(tmpdir) / "with_logo.mp4"
                            overlay_logo(
                                work_path,
                                logo_out,
                                logo_path,
                                x=horiz_logo_x,
                                y=horiz_logo_y,
                                logo_height=80,
                                use_gpu=False,
                            )
                            corte.file.delete(save=False)
                            with open(logo_out, "rb") as f:
                                corte.file.save(
                                    f"job_{analysis.id}_sug_{corte.suggestion_id}_logo.mp4",
                                    File(f),
                                    save=True,
                                )
                            work_path = Path(corte.file.path)
                            logger.info("Corte %s: logo inserido em (%d,%d)", corte.id, horiz_logo_x, horiz_logo_y)
                    except Exception as e:
                        logger.exception("Erro ao inserir logo corte %s: %s", corte.id, e)

            # 4. Queimar legendas (se marcado)
            if not corte.needs_subtitle:
                logger.info("Corte %s: pulando legendas (needs_subtitle=False)", corte.id)
            elif not corte.subtitle_segments:
                logger.info("Corte %s: pulando legendas (subtitle_segments vazio)", corte.id)
            if corte.needs_subtitle and corte.subtitle_segments:
                if work_path.exists():
                    try:
                        with tempfile.TemporaryDirectory() as tmpdir:
                            tmppath = Path(tmpdir)
                            srt_path = tmppath / "subtitles.srt"
                            srt_path.write_text(
                                segments_to_srt(corte.subtitle_segments), encoding="utf-8"
                            )
                            output_tmp = tmppath / "output_with_subs.mp4"
                            burn_subtitles(work_path, srt_path, output_tmp, style)
                            corte.file.delete(save=False)
                            with open(output_tmp, "rb") as f:
                                corte.file.save(
                                    f"job_{analysis.id}_sug_{corte.suggestion_id}_final.mp4",
                                    File(f),
                                    save=True,
                                )
                            logger.info("Corte %s: legendas queimadas", corte.id)
                    except Exception as e:
                        logger.exception("Erro ao queimar legendas corte %s: %s", corte.id, e)
        except Exception as e:
            logger.exception("Erro ao finalizar corte %s: %s", corte.id, e)
        corte.is_finalized = True
        corte.save(update_fields=["is_finalized"])
