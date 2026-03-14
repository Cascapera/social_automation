"""
Task Celery para buscar vídeos automaticamente nos canais de busca.
Executada pelo Beat a cada intervalo; processa no máximo 1 job por factory.
"""
import logging
from django.db.models import Count
from django.utils import timezone
from celery import shared_task

from apps.brands.models import Factory, Brand, SearchChannel, ProcessedChannelVideo, ProcessedYoutubeVideo
from apps.auto_cuts.models import AutoCutAnalysis
from apps.jobs.models import VideoInventoryItem
from apps.auto_cuts.services.youtube_fetch import (
    parse_channel_identifier,
    resolve_channel_id,
    fetch_latest_videos,
    get_channel_info,
    extract_video_id,
    _get_youtube_client,
)
from apps.auto_cuts.tasks import analyze_auto_cuts_task

logger = logging.getLogger(__name__)


def _factory_has_job_in_progress(factory: Factory) -> bool:
    """Verifica se a factory tem análise em andamento (pending, transcribing, analyzing)."""
    brands = list(Brand.objects.filter(factory=factory).values_list("id", flat=True))
    if not brands:
        return False
    return AutoCutAnalysis.objects.filter(
        brand_id__in=brands,
        status__in=["pending", "transcribing", "analyzing"],
    ).exists()


def _count_available_by_brand(factory: Factory) -> dict[int, int]:
    """Retorna {brand_id: count} de vídeos AVAILABLE."""
    qs = (
        VideoInventoryItem.objects.filter(factory=factory, status="AVAILABLE")
        .values("brand_id")
        .annotate(cnt=Count("id"))
    )
    return {r["brand_id"]: r["cnt"] for r in qs}


def _count_available_total(factory: Factory) -> int:
    """Total de vídeos AVAILABLE na factory."""
    return VideoInventoryItem.objects.filter(factory=factory, status="AVAILABLE").count()


def _is_video_already_processed(factory: Factory, video_id: str) -> bool:
    """Verifica se o vídeo já foi processado (ProcessedYoutubeVideo global)."""
    return ProcessedYoutubeVideo.objects.filter(
        factory=factory,
        youtube_video_id=video_id,
    ).exists()


def _get_search_channels_ordered(
    factory: Factory,
    *,
    target_brand_id: int | None = None,
) -> list[SearchChannel]:
    """
    Retorna lista de SearchChannels para tentar, em ordem de prioridade.
    Se target_brand_id: primeiro canais que direcionam para essa brand, depois "todos", depois outros.
    Senão: canais "todos" primeiro, depois os que direcionam para brands específicas.
    """
    channels = list(
        SearchChannel.objects.filter(
            factory=factory,
            is_active=True,
        ).select_related("target_brand").order_by("id")
    )
    if not channels:
        return []

    if target_brand_id:
        for_brand = [c for c in channels if c.target_brand_id == target_brand_id]
        todos = [c for c in channels if c.target_brand_id is None]
        others = [c for c in channels if c.target_brand_id not in (target_brand_id, None)]
        return for_brand + todos + others
    else:
        todos = [c for c in channels if c.target_brand_id is None]
        others = [c for c in channels if c.target_brand_id is not None]
        return todos + others


@shared_task
def check_and_fetch_new_videos_task() -> dict:
    """
    Para cada factory com auto_fetch_enabled=True:
    - Se já tem job em andamento: skip
    - Se total >= max: skip
    - Se alguma brand < min_per_brand ou total < min_total: busca 1 vídeo
    - Máximo 1 novo job por factory por execução
    """
    factories = list(
        Factory.objects.filter(
            is_active=True,
            auto_fetch_enabled=True,
        )
    )
    if not factories:
        return {"factories_checked": 0}

    results = {"factories_checked": len(factories), "jobs_created": 0, "skipped": [], "errors": []}

    if not _get_youtube_client():
        logger.warning("[AUTO_FETCH] API YouTube não configurada (YOUTUBE_API_KEY ou YOUTUBE_CHECK_*)")
        results["errors"].append("API YouTube não configurada")
        return results

    for factory in factories:
        try:
            if _factory_has_job_in_progress(factory):
                results["skipped"].append(f"{factory.name}: job em andamento")
                continue

            total = _count_available_total(factory)
            max_total = factory.auto_fetch_max_total or 100
            if total >= max_total:
                results["skipped"].append(f"{factory.name}: banco cheio ({total} >= {max_total})")
                continue

            by_brand = _count_available_by_brand(factory)
            min_per_brand = factory.auto_fetch_min_per_brand or 3
            min_total = factory.auto_fetch_min_total or 10

            target_brand_id = None
            for brand in Brand.objects.filter(factory=factory):
                cnt = by_brand.get(brand.id, 0)
                if cnt < min_per_brand:
                    target_brand_id = brand.id
                    break

            need_fetch = total < min_total or target_brand_id is not None
            if not need_fetch:
                results["skipped"].append(f"{factory.name}: estoque OK (total={total})")
                continue

            channels_to_try = _get_search_channels_ordered(factory, target_brand_id=target_brand_id)
            if not channels_to_try:
                results["skipped"].append(f"{factory.name}: sem canais de busca ativos")
                continue

            min_age_hours = float(factory.auto_fetch_min_video_age_hours or 24)
            max_age_hours = float(factory.auto_fetch_max_video_age_hours or 168)
            youtube = _get_youtube_client()
            video_to_process = None
            search_channel = None

            for search_channel in channels_to_try:
                channel_id = search_channel.youtube_channel_id
                if not channel_id:
                    channel_id_raw, handle = parse_channel_identifier(search_channel.youtube_channel_url)
                    channel_id = resolve_channel_id(youtube, channel_id_raw, handle)
                    if not channel_id:
                        logger.info(
                            "[AUTO_FETCH] Factory %s: não foi resolver canal %s, tentando próximo",
                            factory.name,
                            search_channel.youtube_channel_url,
                        )
                        continue
                    search_channel.youtube_channel_id = channel_id
                    info = get_channel_info(channel_id)
                    if info:
                        search_channel.channel_title = (info.get("title") or "")[:200]
                    search_channel.last_checked_at = timezone.now()
                    search_channel.save(update_fields=["youtube_channel_id", "channel_title", "last_checked_at", "updated_at"])

                min_duration_minutes = factory.auto_fetch_min_duration_minutes or 50
                min_views = factory.auto_fetch_min_views or 0
                videos = fetch_latest_videos(
                    channel_id,
                    max_results=15,
                    exclude_live=True,
                    min_hours_since_publish=min_age_hours,
                    max_hours_since_publish=max_age_hours,
                    min_duration_minutes=min_duration_minutes,
                    min_views=min_views if min_views > 0 else None,
                )
                for v in videos:
                    vid = v.get("video_id")
                    if not vid:
                        continue
                    if not _is_video_already_processed(factory, vid):
                        video_to_process = v
                        break
                if video_to_process:
                    break

            if not video_to_process or not search_channel:
                channels_tried = len(channels_to_try)
                results["skipped"].append(
                    f"{factory.name}: nenhum vídeo novo em {channels_tried} canal(is) tentado(s)"
                )
                continue

            youtube_url = video_to_process.get("url") or f"https://www.youtube.com/watch?v={video_to_process.get('video_id')}"
            first_brand = Brand.objects.filter(factory=factory).order_by("id").first()
            if not first_brand:
                results["errors"].append(f"{factory.name}: factory sem brands")
                continue

            video_title = (video_to_process.get("title") or "").strip()[:200] or "Auto-fetch"
            target_brand = None
            distribution_mode = "theme"
            if getattr(search_channel, "distribute_by_brands", False):
                distribution_mode = "distribute"
            else:
                target_brand = search_channel.target_brand
            analysis = AutoCutAnalysis.objects.create(
                user=None,
                brand=first_brand,
                target_brand=target_brand,
                distribution_mode=distribution_mode,
                youtube_url=youtube_url,
                name=video_title,
                assunto="",
                convidados="",
                prompt_version=factory.auto_fetch_prompt_version or "viral",
                shorts_target=factory.auto_fetch_shorts_target or 12,
                longs_target=factory.auto_fetch_longs_target or 3,
            )
            ProcessedChannelVideo.objects.get_or_create(
                search_channel=search_channel,
                youtube_video_id=video_to_process["video_id"],
                defaults={"factory": factory},
            )
            ProcessedYoutubeVideo.objects.get_or_create(
                factory=factory,
                youtube_video_id=video_to_process["video_id"],
                defaults={"source": "auto"},
            )
            analyze_auto_cuts_task.delay(analysis.id)
            results["jobs_created"] += 1
            logger.info(
                "[AUTO_FETCH] Factory %s: criada analysis %s para %s",
                factory.name,
                analysis.id,
                youtube_url,
            )

        except Exception as e:
            logger.exception("[AUTO_FETCH] Erro em factory %s: %s", factory.name, e)
            results["errors"].append(f"{factory.name}: {e}")

    return results
