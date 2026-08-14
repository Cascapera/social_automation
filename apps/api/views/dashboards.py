"""Painéis de leitura: métricas e YouTube por factory.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""


from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brands.models import (
    Brand,
    Factory,
)

from ..pagination import StandardResultsSetPagination


def _parse_positive_int(val):
    if val is None or val == "":
        return None
    try:
        i = int(val)
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_bool_flag(val, default=False):
    if val is None or val == "":
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _resolve_factory_brand_scope(factory_id, brand_raw):
    fid = _parse_positive_int(factory_id)
    if not fid:
        return None, None, Response({"detail": "factory_id inválido."}, status=status.HTTP_400_BAD_REQUEST)
    if not Factory.objects.filter(pk=fid).exists():
        return None, None, Response({"detail": "Factory não encontrada."}, status=status.HTTP_404_NOT_FOUND)

    brand_id = _parse_positive_int(brand_raw)
    if brand_id and not Brand.objects.filter(pk=brand_id, factory_id=fid).exists():
        return None, None, Response(
            {"detail": "Brand não pertence à factory indicada."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return fid, brand_id, None


class DashboardMetricsView(APIView):
    """
    Métricas reais do dashboard (AutoCut): vídeos processados, minutos, cortes finalizados.
    Escopo: ?brand=ID ou ?factory=ID (obrigatório um deles). Com ambos, valida que a brand
    pertence à factory e aplica escopo pela brand.
    """

    def get(self, request):
        from ..dashboard_metrics import compute_dashboard_metrics

        brand_id = _parse_positive_int(request.query_params.get("brand"))
        factory_id = _parse_positive_int(request.query_params.get("factory"))
        if not brand_id and not factory_id:
            return Response(
                {"detail": "Informe o parâmetro brand ou factory."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if brand_id and factory_id:
            if not Brand.objects.filter(pk=brand_id, factory_id=factory_id).exists():
                return Response(
                    {"detail": "Brand não pertence à factory indicada."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            factory_id = None

        data = compute_dashboard_metrics(request.user, brand_id, factory_id)
        return Response(data)


class FactoryYoutubeDashboardView(APIView):
    """
    YouTube via Upload Post: resumo agregado por factory (assinantes, views no período, série, top vídeos).
    GET /api/dashboard/factory/<factory_id>/youtube-summary/?period=last_month&brand=<id>&refresh=1
    """

    def get(self, request, factory_id=None):
        from ..factory_youtube_dashboard import (
            build_factory_youtube_dashboard,
            normalize_period_param,
        )

        fid, brand_id, error_response = _resolve_factory_brand_scope(
            factory_id,
            request.query_params.get("brand"),
        )
        if error_response is not None:
            return error_response
        period = normalize_period_param(request.query_params.get("period"))
        refresh = _parse_bool_flag(request.query_params.get("refresh"))
        include_top_posts = _parse_bool_flag(
            request.query_params.get("include_top_posts"),
            default=True,
        )

        data = build_factory_youtube_dashboard(
            fid,
            brand_id=brand_id,
            period=period,
            include_top_posts=include_top_posts,
            force_refresh=refresh,
        )
        return Response(data)


class FactoryYoutubeVideosView(APIView):
    """
    Lista paginada de vídeos YouTube por factory/brand, ordenada por views ou viral_score.
    GET /api/dashboard/factory/<factory_id>/youtube-videos/?period=last_month&brand=<id>&ordering=viral_score&page=1&page_size=10
    """

    pagination_class = StandardResultsSetPagination

    def get(self, request, factory_id=None):
        from ..factory_youtube_dashboard import (
            build_factory_youtube_videos,
            normalize_period_param,
            normalize_video_ordering,
        )

        fid, brand_id, error_response = _resolve_factory_brand_scope(
            factory_id,
            request.query_params.get("brand"),
        )
        if error_response is not None:
            return error_response

        period = normalize_period_param(request.query_params.get("period"))
        ordering = normalize_video_ordering(request.query_params.get("ordering"))
        refresh = _parse_bool_flag(request.query_params.get("refresh"))
        payload = build_factory_youtube_videos(
            fid,
            brand_id=brand_id,
            period=period,
            ordering=ordering,
            force_refresh=refresh,
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(payload.get("results") or [], request, view=self)
        return Response(
            {
                "count": payload.get("count") or 0,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": page or [],
                "meta": payload.get("meta") or {},
                "scope": payload.get("scope") or {},
            }
        )
