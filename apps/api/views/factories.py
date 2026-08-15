"""Factories e canais de busca.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.brands.models import (
    Factory,
    SearchChannel,
)

from ..pagination import StandardResultsSetPagination
from ..serializers import (
    FactorySerializer,
    SearchChannelSerializer,
)


class FactoryViewSet(viewsets.ModelViewSet):
    """CRUD de factories (pool multicanal)."""
    queryset = Factory.objects.all().order_by("name")
    serializer_class = FactorySerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    @action(detail=True, methods=["post"], url_path="trigger-immediate-schedule")
    def trigger_immediate_schedule(self, request, pk=None):
        """
        Dispara o agendamento imediato para a factory.
        Gera agenda para o dia informado (ou dia seguinte se não informado).
        Respeita horários das brands e vídeos disponíveis no banco.
        Body opcional: {"target_date": "YYYY-MM-DD"}
        """
        from datetime import date
        from zoneinfo import ZoneInfo

        from django.utils import timezone

        from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory

        factory = self.get_object()
        factory_tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
        now_local = timezone.now().astimezone(factory_tz)
        default_target = now_local.date() + timedelta(days=1)

        target_date = default_target
        brand_id = None
        if request.data and isinstance(request.data, dict):
            raw = (request.data.get("target_date") or "").strip()
            if raw:
                try:
                    parsed = date.fromisoformat(raw)
                    if parsed < now_local.date():
                        return Response(
                            {"error": "Data não pode ser no passado."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    target_date = parsed
                except (ValueError, TypeError):
                    return Response(
                        {"error": "Data inválida. Use formato YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            bid = request.data.get("brand_id")
            if bid is not None:
                try:
                    brand_id = int(bid)
                except (ValueError, TypeError):
                    pass

        if brand_id:
            from apps.brands.models import Brand
            if not Brand.objects.filter(id=brand_id, factory=factory).exists():
                return Response(
                    {"error": "Brand não pertence a esta factory."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            result = generate_daily_schedule_for_factory(
                factory,
                now_utc=timezone.now(),
                target_date=target_date,
                allow_rerun=True,
                brand_id=brand_id,
                enqueue_immediately=True,
            )
            return Response({
                "created": result.get("created", 0),
                "factory_id": factory.id,
                "target_date": str(target_date),
            })
        except Exception as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _parse_target_date_and_brand(self, request, factory, *, default_target):
        """Lê `target_date` e `brand_id` do corpo. Devolve `(target_date, brand_id, erro)`.

        Mesma validação que o agendamento sempre fez — data no passado e data inválida são
        recusadas, brand tem que pertencer à factory.
        """
        from datetime import date
        from zoneinfo import ZoneInfo

        from django.utils import timezone as dj_timezone

        factory_tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
        now_local = dj_timezone.now().astimezone(factory_tz)

        target_date = default_target
        brand_id = None
        if request.data and isinstance(request.data, dict):
            raw = (request.data.get("target_date") or "").strip()
            if raw:
                try:
                    parsed = date.fromisoformat(raw)
                except (ValueError, TypeError):
                    return None, None, "Data inválida. Use formato YYYY-MM-DD."
                if parsed < now_local.date():
                    return None, None, "Data não pode ser no passado."
                target_date = parsed
            bid = request.data.get("brand_id")
            if bid is not None:
                try:
                    brand_id = int(bid)
                except (ValueError, TypeError):
                    brand_id = None

        if brand_id:
            from apps.brands.models import Brand
            if not Brand.objects.filter(id=brand_id, factory=factory).exists():
                return None, None, "Brand não pertence a esta factory."

        return target_date, brand_id, None

    @action(detail=True, methods=["post"], url_path="immediate-post-preview")
    def immediate_post_preview(self, request, pk=None):
        """
        O que o "Postar Imediato" publicaria para o dia informado, sem publicar nada.

        Roda o mesmo planejamento da execução, então o número que aparece na tela é o
        número que vai ser postado — não uma estimativa paralela.
        Body: {"target_date": "YYYY-MM-DD", "brand_id": <opcional>}
        """
        from apps.jobs.services.immediate_post import preview_immediate_post

        factory = self.get_object()
        factory_tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
        default_target = timezone.now().astimezone(factory_tz).date()
        target_date, brand_id, error = self._parse_target_date_and_brand(
            request, factory, default_target=default_target
        )
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        try:
            return Response(
                preview_immediate_post(factory, target_date=target_date, brand_id=brand_id)
            )
        except Exception as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], url_path="trigger-immediate-post")
    def trigger_immediate_post(self, request, pk=None):
        """
        Publica **agora** os vídeos do dia informado, em vez de esperar o horário do slot.

        Os slots continuam decidindo quantos e quais vídeos entram, e slot cujo horário já
        passou fica de fora; o que entra vai para o YouTube e o Upload-Post na hora, pelas
        mesmas regras de publicação do agendamento.
        Body: {"target_date": "YYYY-MM-DD", "brand_id": <opcional>}
        """
        from apps.jobs.services.immediate_post import run_immediate_post

        factory = self.get_object()
        factory_tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
        default_target = timezone.now().astimezone(factory_tz).date()
        target_date, brand_id, error = self._parse_target_date_and_brand(
            request, factory, default_target=default_target
        )
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        try:
            return Response(
                run_immediate_post(factory, target_date=target_date, brand_id=brand_id)
            )
        except Exception as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"], url_path="youtube-check-connect-url")
    def youtube_check_connect_url(self, request, pk=None):
        """Retorna URL para OAuth da API de busca (YOUTUBE_CHECK_*)."""
        from apps.social.services.youtube_oauth import (
            get_check_client_config,
            get_factory_check_authorization_url,
        )

        factory = self.get_object()
        if not get_check_client_config():
            return Response(
                {"error": "YOUTUBE_CHECK_CLIENT_ID e YOUTUBE_CHECK_CLIENT_SECRET devem estar no .env"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            url = get_factory_check_authorization_url(factory.id)
            return Response({"url": url})
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class SearchChannelViewSet(viewsets.ModelViewSet):
    """CRUD de canais de busca (YouTube) por factory."""
    queryset = SearchChannel.objects.all().select_related("factory", "target_brand")
    serializer_class = SearchChannelSerializer
    pagination_class = StandardResultsSetPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        if factory:
            qs = qs.filter(factory_id=factory)
        return qs.order_by("id")

    def perform_create(self, serializer):
        instance = serializer.save()
        _resolve_search_channel(instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        if "youtube_channel_url" in serializer.validated_data:
            _resolve_search_channel(instance)


def _resolve_search_channel(channel: SearchChannel) -> None:
    """Resolve channel_id e channel_title a partir da URL."""
    from apps.auto_cuts.services.youtube_fetch import (
        _get_youtube_client,
        get_channel_info,
        parse_channel_identifier,
        resolve_channel_id,
    )
    youtube = _get_youtube_client()
    if not youtube:
        return
    channel_id_raw, handle = parse_channel_identifier(channel.youtube_channel_url)
    resolved = resolve_channel_id(youtube, channel_id_raw, handle)
    if resolved:
        channel.youtube_channel_id = resolved
        info = get_channel_info(resolved)
        if info:
            channel.channel_title = (info.get("title") or "")[:200]
        channel.last_checked_at = timezone.now()
        channel.save(update_fields=["youtube_channel_id", "channel_title", "last_checked_at", "updated_at"])
