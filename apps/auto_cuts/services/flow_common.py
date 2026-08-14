"""Helpers compartilhados pelos fluxos de auto_cuts (refactor.md R-19).

Aqui ficam as funções que **os dois fluxos grandes usam** — análise (`analysis_flow`)
e finalização (ainda em `tasks.py`, sai no PR seguinte). Elas foram extraídas de
`apps/auto_cuts/tasks.py` sem nenhuma alteração de comportamento: mesmo corpo, mesmos
nomes, mesmas mensagens.

O módulo existe para quebrar o ciclo de import que apareceria se `analysis_flow`
importasse de `tasks.py`: a dependência correta é `tasks.py → services/`, nunca o
contrário. A única exceção está em `_queue_analysis_finalization`, que precisa do objeto
da task Celery para enfileirar — o import é adiado para dentro da função, o mesmo padrão
que `services/recovery.py` já usa.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.utils import DatabaseError

logger = logging.getLogger(__name__)


def _append_convidados(title: str, convidados: str) -> str:
    """Append guest name(s) to a title when the analysis has convidados filled.

    Example: "Sem Falsidade no Sexo! 💋🔥" + "Renato Albani"
          -> "Sem Falsidade no Sexo! 💋🔥 + Renato Albani"
    """
    guest = (convidados or "").strip()
    if not guest:
        return title
    base = (title or "").rstrip()
    return f"{base} + {guest}"


def _safe_save_analysis(analysis, update_fields):
    """
    Save analysis; returns False if row was deleted (caller should return).
    Avoids DatabaseError when job is deleted during processing.
    """
    try:
        analysis.save(update_fields=update_fields)
        return True
    except DatabaseError as e:
        if "did not affect any rows" in str(e):
            return False
        raise


def _resolve_finalization_vertical_mode(analysis) -> str:
    vert_mode = (getattr(analysis, "vertical_mode", None) or "").strip()
    if vert_mode:
        return vert_mode
    brand = getattr(analysis, "brand", None)
    return getattr(brand, "vertical_mode", None) or "zoom_crop"


def _mark_analysis_done(analysis) -> None:
    analysis.status = "done"
    analysis.progress_message = "Concluído"
    analysis.progress = 100
    analysis.error = ""
    if not _safe_save_analysis(analysis, ["status", "progress_message", "progress", "error"]):
        return
    try:
        from apps.auto_cuts.services.youtube_fetch import register_manual_youtube_success

        register_manual_youtube_success(analysis)
    except Exception:
        logger.exception(
            "[FLUXO] register_manual_youtube_success failed (analysis_id=%s)",
            getattr(analysis, "id", None),
        )


def _queue_analysis_finalization(analysis) -> None:
    # Import adiado de propósito: `services/` não pode importar `tasks.py` no topo do
    # módulo sem criar ciclo. Mesmo padrão de services/recovery.py.
    from apps.auto_cuts.tasks import finalizar_auto_cut_task

    analysis.status = "finalizing"
    analysis.progress_message = "Finalizando cortes e sincronizando inventário..."
    analysis.progress = min(99, max(int(getattr(analysis, "progress", 0) or 0), 95))
    analysis.error = ""
    if not _safe_save_analysis(analysis, ["status", "progress_message", "progress", "error"]):
        return

    finalizar_auto_cut_task.apply_async(
        args=[analysis.id],
        kwargs={
            "vertical_mode": _resolve_finalization_vertical_mode(analysis),
            "horizontal_logo_x": 20,
            "horizontal_logo_y": 20,
        },
        queue=settings.CELERY_QUEUE_RENDER,
    )


def _sanitize_long_overlay_fk(analysis) -> bool:
    """
    If long_overlay_asset_id points to deleted BrandAsset, clear FK and disable overlay.
    Avoids IntegrityError on job save (e.g. after YouTube download with file.save).
    """
    from apps.brands.models import BrandAsset

    pk = getattr(analysis, "long_overlay_asset_id", None)
    if not pk:
        return False
    if BrandAsset.objects.filter(pk=pk).exists():
        return False
    analysis.long_overlay_asset_id = None
    analysis.long_overlay_enabled = False
    return True


def _resolve_target_brand_for_suggestion(analysis, suggestion):
    """
    Resolve destination brand via target_brand (priority), distribute, or category (Factory 1:1).
    - target_brand set: all cuts go to that channel.
    - distribution_mode=distribute: pick brand with fewest AVAILABLE videos in bank.
    - distribution_mode=theme: map from AI theme_category.
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
        from django.db.models import Count

        from apps.brands.models import Brand
        from apps.jobs.models import VideoInventoryItem

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
    Create/update factory video bank item when a cut is finalized.
    When analysis.target_brand_id is set, all cuts go to that brand
    (ignores suggestion theme_category).
    """
    if not corte or not getattr(corte, "analysis_id", None):
        return
    from apps.auto_cuts.models import AutoCutAnalysis

    analysis = AutoCutAnalysis.objects.filter(id=corte.analysis_id).first()
    if not analysis:
        return
    suggestion = corte.suggestion
    target_brand = _resolve_target_brand_for_suggestion(analysis, suggestion)
    if not target_brand:
        if getattr(analysis, "target_brand_id", None):
            logger.warning(
                "[FLUXO] Cut %s: target_brand_id=%s set but brand not found. Check that the brand exists.",
                getattr(corte, "id", None),
                analysis.target_brand_id,
            )
        else:
            logger.warning(
                "[FLUXO] Cut %s skipped for inventory: no valid routing (theme=%s). "
                "Use 'Direct all cuts to' to send everything to one brand.",
                getattr(corte, "id", None),
                getattr(suggestion, "theme_category", "") if suggestion else "",
            )
        return
    from apps.jobs.models import VideoInventoryItem

    cut_type = (getattr(suggestion, "cut_type", "") or "").strip().lower()
    video_type = "SHORT" if cut_type == "short" else "LONG"
    raw_data = getattr(suggestion, "raw_data", None) or {}
    suggested_description = str(raw_data.get("suggested_description") or "").strip()[:5000]
    defaults = {
        "factory_id": target_brand.factory_id,
        "brand_id": target_brand.id,
        "video_type": video_type,
        "title": (getattr(suggestion, "title", "") or "")[:220],
        "description": suggested_description,
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
            "[FLUXO] Cut %s → inventory brand_id=%s (target_brand override)",
            getattr(corte, "id", None),
            target_brand.id,
        )
