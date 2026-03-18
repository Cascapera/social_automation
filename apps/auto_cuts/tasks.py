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
from django.utils import timezone

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
from apps.auto_cuts.services.grok import analyze_chunks_in_one_request, analyze_ready_cut_metadata

# Vídeos maiores que isso usam transcrição por chunks (evita crash de memória)
CHUNKED_TRANSCRIPTION_THRESHOLD_SEC = 10 * 60  # 10 min
VIRAL_SHORT_MIN_SEC = 30
VIRAL_SHORT_MAX_SEC = 60
VIRAL_LONG_MIN_SEC = 8 * 60
VIRAL_LONG_MAX_SEC = 15 * 60
EDUCATIONAL_SHORT_MAX_SEC = 180
THEME_CATEGORY_NORMALIZATION = {
    "business_money": "BUSINESS_MONEY",
    "business": "BUSINESS_MONEY",
    "money": "BUSINESS_MONEY",
    "negocios_dinheiro": "BUSINESS_MONEY",
    "negocios": "BUSINESS_MONEY",
    "dinheiro": "BUSINESS_MONEY",
    "psychology_relationships": "PSYCHOLOGY_RELATIONSHIPS",
    "psychology": "PSYCHOLOGY_RELATIONSHIPS",
    "relationships": "PSYCHOLOGY_RELATIONSHIPS",
    "psicologia_relacionamentos": "PSYCHOLOGY_RELATIONSHIPS",
    "psicologia": "PSYCHOLOGY_RELATIONSHIPS",
    "relacionamentos": "PSYCHOLOGY_RELATIONSHIPS",
    "stories_curiosities": "STORIES_CURIOSITIES",
    "stories": "STORIES_CURIOSITIES",
    "curiosities": "STORIES_CURIOSITIES",
    "historias_curiosidades": "STORIES_CURIOSITIES",
    "historias": "STORIES_CURIOSITIES",
    "curiosidades": "STORIES_CURIOSITIES",
    "controversies_debate": "CONTROVERSIES_DEBATE",
    "controversies": "CONTROVERSIES_DEBATE",
    "debate": "CONTROVERSIES_DEBATE",
    "polemicas_debate": "CONTROVERSIES_DEBATE",
    "polemicas": "CONTROVERSIES_DEBATE",
    "comedy_humor": "COMEDY_HUMOR",
    "comedy": "COMEDY_HUMOR",
    "humor": "COMEDY_HUMOR",
}
ALL_THEME_CATEGORIES = [
    "BUSINESS_MONEY",
    "PSYCHOLOGY_RELATIONSHIPS",
    "STORIES_CURIOSITIES",
    "CONTROVERSIES_DEBATE",
    "COMEDY_HUMOR",
]


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


def _normalize_theme_category(value: str, fallback: str = "") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    if raw in (
        "BUSINESS_MONEY",
        "PSYCHOLOGY_RELATIONSHIPS",
        "STORIES_CURIOSITIES",
        "CONTROVERSIES_DEBATE",
        "COMEDY_HUMOR",
    ):
        return raw
    key = (
        raw.lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
    )
    key = "_".join([p for p in key.split("_") if p])
    return THEME_CATEGORY_NORMALIZATION.get(key, fallback)


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


def _resolve_target_brand_for_suggestion(analysis, suggestion):
    """
    Resolve a brand de destino via target_brand (prioridade), distribute ou categoria (Factory 1:1).
    - target_brand definido: todos os cortes vão para esse canal.
    - distribution_mode=distribute: envia para a brand com menos vídeos AVAILABLE no banco.
    - distribution_mode=theme: usa theme_category da IA para mapear.
    """
    target_id = getattr(analysis, "target_brand_id", None)
    if target_id:
        from apps.brands.models import Brand
        target = Brand.objects.filter(id=target_id).first()
        if target:
            return target
    target = getattr(analysis, "target_brand", None)
    if target:
        return target
    base_brand = getattr(analysis, "brand", None)
    if not base_brand:
        return None
    factory_id = getattr(base_brand, "factory_id", None)
    if not factory_id:
        return base_brand

    distribution_mode = getattr(analysis, "distribution_mode", "") or "theme"
    if distribution_mode == "distribute":
        from apps.brands.models import Brand
        from apps.jobs.models import VideoInventoryItem
        from django.db.models import Count

        brands = list(Brand.objects.filter(factory_id=factory_id).values_list("id", flat=True))
        if not brands:
            return base_brand
        counts = (
            VideoInventoryItem.objects.filter(
                factory_id=factory_id,
                brand_id__in=brands,
                status="AVAILABLE",
            )
            .values("brand_id")
            .annotate(cnt=Count("id"))
        )
        count_by_brand = {r["brand_id"]: r["cnt"] for r in counts}
        min_count = min(count_by_brand.get(bid, 0) for bid in brands)
        candidates = [bid for bid in brands if count_by_brand.get(bid, 0) == min_count]
        chosen_id = min(candidates)
        return Brand.objects.filter(id=chosen_id).first() or base_brand

    category = (getattr(suggestion, "theme_category", "") or "").strip()
    if not category:
        return base_brand
    from apps.brands.models import Brand

    mapped = Brand.objects.filter(
        factory_id=factory_id,
        theme_category=category,
    ).first()
    return mapped or base_brand


def _sync_inventory_item_from_corte(corte):
    """
    Cria/atualiza item no banco de vídeos da factory ao finalizar corte.
    Quando analysis.target_brand_id está definido, todos os cortes vão para essa brand
    (ignora theme_category da sugestão).
    """
    if not corte or not getattr(corte, "analysis_id", None):
        return
    from apps.auto_cuts.models import AutoCutAnalysis

    analysis = AutoCutAnalysis.objects.filter(id=corte.analysis_id).first()
    if not analysis:
        return
    suggestion = corte.suggestion
    target_brand = _resolve_target_brand_for_suggestion(analysis, suggestion)
    if not target_brand or not getattr(target_brand, "factory_id", None):
        if getattr(analysis, "target_brand_id", None):
            logger.warning(
                "[FLUXO] Corte %s: target_brand_id=%s definido mas brand não encontrada. Verifique se a brand existe.",
                getattr(corte, "id", None),
                analysis.target_brand_id,
            )
        else:
            logger.warning(
                "[FLUXO] Corte %s ignorado no inventário: sem roteamento válido (theme=%s). "
                "Use 'Direcionar todos os cortes para' para enviar todos à mesma brand.",
                getattr(corte, "id", None),
                getattr(suggestion, "theme_category", "") if suggestion else "",
            )
        return
    from apps.jobs.models import VideoInventoryItem

    cut_type = (getattr(suggestion, "cut_type", "") or "").strip().lower()
    video_type = "SHORT" if cut_type == "short" else "LONG"
    defaults = {
        "factory_id": target_brand.factory_id,
        "brand_id": target_brand.id,
        "video_type": video_type,
        "title": (getattr(suggestion, "title", "") or "")[:220],
        "description": "",
        "virality_score": getattr(suggestion, "virality_score", None),
        "source_asset_id": getattr(suggestion, "source_asset_id", "") or "",
        "source_metadata": {
            "analysis_id": analysis.id,
            "suggestion_id": suggestion.id,
            "theme_category": getattr(suggestion, "theme_category", "") or "",
        },
        "status": "AVAILABLE" if corte.is_finalized and corte.file else "FAILED",
        "last_error": "" if (corte.is_finalized and corte.file) else "Corte sem mídia finalizada",
    }
    VideoInventoryItem.objects.update_or_create(
        auto_cut_corte=corte,
        defaults=defaults,
    )
    if getattr(analysis, "target_brand_id", None):
        logger.info(
            "[FLUXO] Corte %s → inventário brand_id=%s (target_brand direcionado)",
            getattr(corte, "id", None),
            target_brand.id,
        )


def _filter_factory_routable_items(analysis, items: list[dict]) -> tuple[list[dict], int, int]:
    """
    Em contexto de factory:
    - remove itens sem categoria válida;
    - remove itens cuja categoria não possui brand mapeada;
    - retorna (itens_validos, ignorados_sem_categoria, ignorados_sem_mapeamento).
    Quando target_brand está definido, passa todos os itens sem filtrar.
    """
    if getattr(analysis, "target_brand_id", None):
        return list(items or []), 0, 0
    if (getattr(analysis, "distribution_mode", "") or "").strip() == "distribute":
        return list(items or []), 0, 0
    base_brand = getattr(analysis, "brand", None)
    factory_id = getattr(base_brand, "factory_id", None) if base_brand else None
    if not factory_id:
        return list(items or []), 0, 0

    from apps.brands.models import Brand

    category_set = {
        b.theme_category
        for b in Brand.objects.filter(factory_id=factory_id).exclude(theme_category="")
    }
    missing_category = 0
    unmapped_count = 0
    valid_items: list[dict] = []

    for item in (items or []):
        normalized = _normalize_theme_category(item.get("theme_category"), fallback="")
        if not normalized:
            missing_category += 1
            continue
        if normalized not in category_set:
            unmapped_count += 1
            continue
        item_copy = dict(item)
        item_copy["theme_category"] = normalized
        valid_items.append(item_copy)

    return valid_items, missing_category, unmapped_count


def _allowed_theme_categories_for_analysis(analysis) -> list[str]:
    """
    Em contexto de factory, retorna apenas categorias mapeadas nas brands da factory.
    Fora de factory, retorna o conjunto completo padrão.
    """
    base_brand = getattr(analysis, "brand", None)
    factory_id = getattr(base_brand, "factory_id", None) if base_brand else None
    if not factory_id:
        return list(ALL_THEME_CATEGORIES)
    from apps.brands.models import Brand

    mapped = sorted(
        {
            str(b.theme_category or "").strip()
            for b in Brand.objects.filter(factory_id=factory_id).exclude(theme_category="")
            if str(b.theme_category or "").strip()
        }
    )
    return mapped or list(ALL_THEME_CATEGORIES)


def _is_factory_processing_paused(analysis) -> bool:
    brand = getattr(analysis, "brand", None)
    if not brand:
        return False
    factory = getattr(brand, "factory", None)
    return bool(factory and getattr(factory, "processing_paused", False))


def _process_ready_cuts_flow(analysis, duration_sec: float, segments: list) -> None:
    """
    Fluxo para cortes prontos: vídeo já editado.
    Transcreve, chama LLM para metadata (título, thumbnail), copia vídeo sem extrair,
    gera thumbnail e finaliza.
    """
    import shutil
    from apps.auto_cuts.models import AutoCutSuggestion
    from apps.jobs.services.ffmpeg import seconds_to_tc

    analysis.status = "analyzing"
    analysis.progress_message = "Analisando metadata com IA..."
    analysis.progress = 20
    analysis.save(update_fields=["status", "progress_message", "progress"])

    transcript = analysis.transcript or ""
    metadata = analyze_ready_cut_metadata(transcript, duration_sec)
    title = metadata.get("title") or "Vídeo"
    # LLM retorna 1-10; normalizamos para 0-100 (escala usada no resto do sistema)
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

    analysis.status = "done"
    analysis.progress_message = "Concluído"
    analysis.progress = 100
    analysis.save(update_fields=["status", "progress_message", "progress"])

    vert_mode = (getattr(analysis, "vertical_mode", None) or "").strip() or None
    if not vert_mode:
        brand = getattr(analysis, "brand", None)
        vert_mode = getattr(brand, "vertical_mode", None) or "zoom_crop"
    finalizar_auto_cut_task.run(
        analysis.id,
        vertical_mode=vert_mode,
        horizontal_insert_logo=True,
        horizontal_logo_x=20,
        horizontal_logo_y=20,
    )
    logger.info("[FLUXO] Ready cuts concluído com sucesso.")


def _is_brand_only(analysis) -> bool:
    """True quando target_brand está definido ou a brand não tem factory (conteúdo exclusivo da marca)."""
    if getattr(analysis, "target_brand_id", None):
        return True
    brand = getattr(analysis, "brand", None)
    if not brand:
        return False
    return getattr(brand, "factory_id", None) is None


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

    # Pausa cooperativa da fila: não interrompe job em execução, apenas evita
    # iniciar novos jobs enquanto a factory estiver pausada.
    if _is_factory_processing_paused(analysis):
        analysis.status = "pending"
        analysis.progress_message = "Fila de jobs pausada para esta factory. Aguardando retomada..."
        analysis.progress = 0
        analysis.error = ""
        if _safe_save_analysis(analysis, ["status", "progress_message", "progress", "error"]):
            self.apply_async(args=[analysis_id], countdown=60)
        logger.info("[FLUXO] Analysis %s adiada: factory com processing_paused.", analysis_id)
        return

    youtube_url = (analysis.youtube_url or "").strip()
    pv = (analysis.prompt_version or "viral").strip().lower()
    transcript_lang = "en" if pv in ("viral_en", "educational_en", "viral_translate") else "pt"
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

        # Fluxo "cortes prontos": vídeo já editado, só precisa de metadata (título, thumbnail)
        if getattr(analysis, "is_ready_cuts", False):
            _process_ready_cuts_flow(analysis, duration_sec, segments)
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
        brand_only = _is_brand_only(analysis)
        allowed_theme_categories = _allowed_theme_categories_for_analysis(analysis)
        if getattr(analysis, "target_brand_id", None):
            target_brand_obj = getattr(analysis, "target_brand", None)
            factory = getattr(target_brand_obj, "factory", None) if target_brand_obj else None
            factory_name = getattr(factory, "name", None) or "?"
            brand_name = getattr(target_brand_obj, "name", None) or f"Brand #{analysis.target_brand_id}"
            logger.info(
                "[FLUXO] Factory (%s) : %s : theme_category da LLM será ignorado (conteúdo exclusivo da marca).",
                factory_name,
                brand_name,
            )
        elif brand_only:
            base_brand = getattr(analysis, "brand", None)
            factory = getattr(base_brand, "factory", None) if base_brand else None
            if factory:
                factory_name = getattr(factory, "name", None) or "?"
                logger.info(
                    "[FLUXO] Factory (%s) : todos : theme_category da LLM será ignorado (conteúdo exclusivo da marca).",
                    factory_name,
                )
            else:
                logger.info("[FLUXO] Brand sem factory: theme_category da LLM será ignorado (conteúdo exclusivo da marca).")
        else:
            logger.info(
                "[FLUXO] Categorias permitidas para roteamento neste job: %s",
                ", ".join(allowed_theme_categories),
            )
        final = None
        for attempt in range(MAX_RETRIES):
            try:
                final = analyze_chunks_in_one_request(
                    chunks,
                    assunto=analysis.assunto or "",
                    convidados=analysis.convidados or "",
                    prompt_version=analysis.prompt_version or "viral",
                    enforce_minimum=(attempt < MAX_RETRIES - 1),
                    allowed_theme_categories=allowed_theme_categories,
                    brand_only=brand_only,
                    analysis_id=analysis.id,
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
        is_viral_prompt = pv in ("viral", "viral_en", "viral_translate")
        is_educational_prompt = pv in ("educational", "educational_en")
        shorts_limit = max(1, min(30, int(getattr(analysis, "shorts_target", 12) or 12)))
        longs_limit = max(1, min(10, int(getattr(analysis, "longs_target", 3) or 3)))
        from apps.jobs.services.ffmpeg import tc_to_seconds, seconds_to_tc

        candidate_shorts_source = final.get("candidate_shorts") or []
        ranked_shorts_source = final.get("ranked_shorts") or []
        if is_viral_prompt:
            # Para viral, prioriza o pool maior (candidate_shorts), pois o ranked_shorts
            # pode vir parcial mesmo quando há muitos candidatos válidos.
            shorts_source = candidate_shorts_source or ranked_shorts_source
        else:
            shorts_source = ranked_shorts_source or candidate_shorts_source
        ranked_shorts = _sort_by_virality(shorts_source)[:shorts_limit]
        ranked_longs = _sort_by_virality(final.get("final_long_cuts") or [])[:longs_limit]
        source_asset_id = ""
        if getattr(analysis, "source_id", None):
            source_asset_id = str(analysis.source_id)
        elif (analysis.youtube_url or "").strip():
            source_asset_id = (analysis.youtube_url or "").strip()
        elif getattr(analysis, "id", None):
            source_asset_id = f"analysis:{analysis.id}"

        # Em contexto factory, ignora itens sem categoria válida/mapeamento.
        ranked_shorts, shorts_ignored_missing_theme, shorts_ignored_unmapped = _filter_factory_routable_items(
            analysis, ranked_shorts
        )
        ranked_longs, longs_ignored_missing_theme, longs_ignored_unmapped = _filter_factory_routable_items(
            analysis, ranked_longs
        )
        ignored_total = (
            shorts_ignored_missing_theme
            + shorts_ignored_unmapped
            + longs_ignored_missing_theme
            + longs_ignored_unmapped
        )
        if ignored_total:
            logger.warning(
                "[FLUXO] Analysis %s: %s corte(s) ignorado(s) por theme_category inválida/sem mapeamento "
                "(shorts sem tema=%s, shorts sem map=%s, longs sem tema=%s, longs sem map=%s).",
                analysis.id,
                ignored_total,
                shorts_ignored_missing_theme,
                shorts_ignored_unmapped,
                longs_ignored_missing_theme,
                longs_ignored_unmapped,
            )

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
            brand_for_theme = getattr(analysis, "target_brand", None) or getattr(analysis, "brand", None)
            theme_for_suggestion = (
                (getattr(brand_for_theme, "theme_category", None) or "").strip()
                if brand_only and brand_for_theme
                else (item.get("theme_category") or "")
            )
            sug = AutoCutSuggestion.objects.create(
                analysis=analysis,
                cut_type="short",
                start_tc=start_tc,
                end_tc=end_tc,
                title=item.get("title") or item.get("suggested_title", ""),
                reason=item.get("reason") or item.get("main_topic", ""),
                hook=item.get("hook") or item.get("hook_sentence", ""),
                virality_score=_normalize_virality_score(item.get("virality_score")),
                theme_category=theme_for_suggestion,
                source_asset_id=source_asset_id,
                rank=rank,
                duration_seconds=duration_seconds,
                raw_data=item,
            )
            suggestions_created.append((sug, "vertical"))

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

            brand_for_theme = getattr(analysis, "target_brand", None) or getattr(analysis, "brand", None)
            theme_for_long = (
                (getattr(brand_for_theme, "theme_category", None) or "").strip()
                if brand_only and brand_for_theme
                else (item.get("theme_category") or "")
            )
            sug = AutoCutSuggestion.objects.create(
                analysis=analysis,
                cut_type="long",
                start_tc=start_tc,
                end_tc=end_tc,
                title=item.get("title_suggestion") or item.get("suggested_title") or item.get("title", ""),
                reason=item.get("reason") or item.get("main_topic", ""),
                virality_score=_normalize_virality_score(item.get("virality_score")),
                theme_category=theme_for_long,
                source_asset_id=source_asset_id,
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
            raw_item = getattr(sug, "raw_data", None) or {}
            subtitle_segments_pt = raw_item.get("subtitle_segments_pt") if isinstance(raw_item, dict) else []
            if pv == "viral_translate" and subtitle_segments_pt:
                # Usar legendas traduzidas pelo Grok (timestamps absolutos → relativos ao corte)
                subtitle_segments = []
                for seg in subtitle_segments_pt:
                    s_start = float(seg.get("start", 0))
                    s_end = float(seg.get("end", 0))
                    if s_end <= cut_start_sec or s_start >= cut_end_sec:
                        continue
                    new_start = max(0.0, s_start - cut_start_sec)
                    new_end = min(cut_duration, s_end - cut_start_sec)
                    text = (seg.get("text") or "").strip()
                    if text:
                        subtitle_segments.append({"start": new_start, "end": new_end, "text": text})
            else:
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
                # Fluxo factory-first: cortes já entram para finalização automática.
                user_wants_finalize=True,
                is_finalized=False,
                subtitle_segments=subtitle_segments,
            )
            with open(out_path, "rb") as f:
                corte.file.save(out_path.name, File(f), save=True)
            target_brand = _resolve_target_brand_for_suggestion(analysis, sug)
            generated_thumb = generate_auto_thumbnail(corte, target_brand=target_brand)
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
            return
        # Finaliza no mesmo fluxo para garantir que o job seja concluído
        # (cortes finalizados + inventário) antes do próximo job pesado.
        vert_mode = (getattr(analysis, "vertical_mode", None) or "").strip() or None
        if not vert_mode:
            brand = getattr(analysis, "brand", None)
            vert_mode = getattr(brand, "vertical_mode", None) or "zoom_crop"
        finalizar_auto_cut_task.run(
            analysis.id,
            vertical_mode=vert_mode,
            horizontal_insert_logo=True,
            horizontal_logo_x=20,
            horizontal_logo_y=20,
        )
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
    "size": 12,
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
    # Shorts: legendas na parte inferior (acima dos botões do YouTube), não no topo
    # MarginV = distância da borda inferior. ~120-150px mantém na área inferior visível.
    style["position"] = "bottom"
    style["margin_v"] = style.get("margin_v", 140)
    vert_mode = vertical_mode or "zoom_crop"
    bg_color = (background_color or "#000000").strip()
    link_text = (custom_text or "").strip()
    title_font = 36 if font_size_title is None else max(12, min(96, int(font_size_title)))
    text_font = 28 if font_size_text is None else max(12, min(72, int(font_size_text)))
    title_clr = (title_color or "#FFFFFF").strip()
    text_clr = (text_color or "#FFFFFF").strip()
    # Logo como marca d'água: canto sup esq, 40px margem, 80% opacidade
    horiz_logo_x = max(0, min(2000, int(horizontal_logo_x or 40)))
    horiz_logo_y = max(0, min(1200, int(horizontal_logo_y or 40)))
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
                            logo_path=logo_path,
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

            # 3. Marca d'água: logo em todos os vídeos (shorts e longs), canto sup esq, 80% opacidade
            if corte.format == "horizontal" and logo_path and logo_path.exists():
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
                                logo_height=160,
                                opacity=0.8,
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
        try:
            _sync_inventory_item_from_corte(corte)
        except Exception as e:
            logger.exception("Erro ao sincronizar inventário do corte %s: %s", corte.id, e)

    # Inventário já foi sincronizado acima. Agendamento automático ocorre SOMENTE às 19h
    # via cron (generate_daily_factory_schedules_task). Não dispara aqui para evitar
    # agendar novos cortes fora do horário esperado.
