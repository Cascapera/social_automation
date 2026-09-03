"""Brands, categorias, assets, contas sociais e credenciais.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""

from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.brands.models import (
    Brand,
    BrandAsset,
    BrandCategory,
    BrandSocialAccount,
    BrandYouTubeCredential,
    Factory,
)

from ..pagination import StandardResultsSetPagination
from ..serializers import (
    BrandAssetSerializer,
    BrandCategorySerializer,
    BrandSerializer,
    BrandSocialAccountSerializer,
    BrandYouTubeCredentialSerializer,
)


class BrandCategoryViewSet(viewsets.ModelViewSet):
    """
    CRUD de categorias temáticas por factory.
    - DELETE é soft-delete (is_active=False). Bloqueia se alguma Brand ainda usar o code.
    - Por padrão lista só categorias ativas. Use ?include_inactive=1 para listar todas.
    - code é imutável; label é editável.
    """
    queryset = BrandCategory.objects.all()
    serializer_class = BrandCategorySerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        if factory:
            qs = qs.filter(factory_id=factory)
        include_inactive = (self.request.query_params.get("include_inactive") or "").strip() in ("1", "true", "True")
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by("factory_id", "label")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        brands_using = Brand.objects.filter(
            factory_id=instance.factory_id,
            theme_category=instance.code,
        ).values_list("id", "name")
        brands_list = list(brands_using)
        if brands_list:
            names = ", ".join(name for _, name in brands_list)
            return Response(
                {
                    "error": (
                        "Não é possível excluir: a categoria está em uso por "
                        f"{len(brands_list)} brand(s): {names}. "
                        "Troque a categoria dessas brands antes de excluir."
                    ),
                    "in_use_by_brand_ids": [bid for bid, _ in brands_list],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="reactivate")
    def reactivate(self, request, pk=None):
        instance = self.get_object()
        if instance.is_active:
            return Response(self.get_serializer(instance).data)
        instance.is_active = True
        instance.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(instance).data)


class BrandViewSet(viewsets.ModelViewSet):
    """Lista e cria marcas."""
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        if factory:
            qs = qs.filter(factory_id=factory)
        return qs

    @action(detail=True, methods=["post"], url_path="trigger-immediate-schedule")
    def trigger_immediate_schedule(self, request, pk=None):
        """
        Agendamento imediato para uma marca (com ou sem factory).
        Para marcas sem factory, cria uma factory pessoal automaticamente.
        Body: {"target_date": "YYYY-MM-DD"}
        """
        from datetime import date
        from zoneinfo import ZoneInfo

        from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory

        brand = self.get_object()
        tz_name = (brand.factory.timezone if brand.factory else None) or "America/Sao_Paulo"
        tz = ZoneInfo(tz_name)
        now_local = timezone.now().astimezone(tz)
        default_target = now_local.date() + timedelta(days=1)

        target_date = default_target
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

        factory = brand.factory
        if not factory:
            factory, created = Factory.objects.get_or_create(
                name=f"{brand.name} (pessoal #{brand.id})",
                defaults={
                    "timezone": "America/Sao_Paulo",
                    "is_active": True,
                    "scheduling_paused": True,
                },
            )
            brand.factory = factory
            brand.save(update_fields=["factory"])
            if not created and not factory.scheduling_paused:
                factory.scheduling_paused = True
                factory.save(update_fields=["scheduling_paused"])

        try:
            result = generate_daily_schedule_for_factory(
                factory,
                now_utc=timezone.now(),
                target_date=target_date,
                allow_rerun=True,
                brand_id=brand.id,
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

    @action(detail=True, methods=["get"])
    def social_accounts(self, request, pk=None):
        """Lista contas sociais conectadas à marca."""
        brand = self.get_object()
        accounts = BrandSocialAccount.objects.filter(brand=brand)
        serializer = BrandSocialAccountSerializer(accounts, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def youtube_connect_url(self, request, pk=None):
        """Retorna URL para iniciar OAuth YouTube (frontend redireciona)."""
        from apps.social.services.youtube_oauth import get_authorization_url, get_client_config

        brand = self.get_object()
        youtube_credential_id = request.query_params.get("youtube_credential_id")
        youtube_credential = None
        if youtube_credential_id:
            youtube_credential = BrandYouTubeCredential.objects.filter(
                id=youtube_credential_id,
                brand=brand,
            ).first()
            if not youtube_credential:
                return Response(
                    {"error": "Credencial YouTube não encontrada para esta brand"},
                    status=status.HTTP_404_NOT_FOUND,
                )
        try:
            config = get_client_config(brand=brand, youtube_credential=youtube_credential)
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not config:
            return Response(
                {"error": "OAuth não configurado"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        url = get_authorization_url(
            brand.id,
            youtube_credential.id if youtube_credential else None,
        )
        return Response({"url": url})

    @action(detail=True, methods=["patch"], url_path="youtube-description")
    def youtube_description(self, request, pk=None):
        """Atualiza configurações de descrição/infantil do YouTube por marca."""
        brand = self.get_object()
        data = request.data or {}
        fields_to_update = []
        if "youtube_description_extra" in data:
            brand.youtube_description_extra = str(data.get("youtube_description_extra") or "")
            fields_to_update.append("youtube_description_extra")
        if "youtube_made_for_kids" in data:
            raw = data.get("youtube_made_for_kids")
            brand.youtube_made_for_kids = str(raw).lower() in ("1", "true", "yes", "on")
            fields_to_update.append("youtube_made_for_kids")
        if fields_to_update:
            brand.save(update_fields=fields_to_update)
        return Response(
            {
                "id": brand.id,
                "youtube_description_extra": brand.youtube_description_extra,
                "youtube_made_for_kids": brand.youtube_made_for_kids,
            }
        )


class BrandSocialAccountViewSet(viewsets.ModelViewSet):
    """Lista e remove contas sociais. Filtro: ?brand=X"""
    queryset = BrandSocialAccount.objects.all()
    serializer_class = BrandSocialAccountSerializer
    http_method_names = ["get", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(brand_id=brand)
        return qs


class BrandYouTubeCredentialViewSet(viewsets.ModelViewSet):
    """Credenciais YouTube por brand para fallback de cota."""
    queryset = BrandYouTubeCredential.objects.all()
    serializer_class = BrandYouTubeCredentialSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset().select_related("brand")
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(brand_id=brand)
        return qs.order_by("order_index", "id")

    def _handle_credential_error(self, exc):
        """Evita 500: devolve 400 com mensagem clara para erros de cadastro."""
        if isinstance(exc, IntegrityError):
            msg = str(exc) or "Conflito ao salvar."
            if "uniq_brand_youtube_credential_order" in msg or "order_index" in msg.lower():
                return Response(
                    {"error": "Já existe outra credencial com essa Ordem nesta marca. Use uma ordem diferente."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"error": f"Conflito de dados: {msg[:200]}"}, status=status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, ValueError) and "SOCIAL_ENCRYPTION_KEY" in str(exc):
            return Response(
                {"error": "Chave de criptografia não configurada. Defina SOCIAL_ENCRYPTION_KEY no .env."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except (IntegrityError, ValueError) as e:
            resp = self._handle_credential_error(e)
            if resp is not None:
                return resp
            raise
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_update(serializer)
        except (IntegrityError, ValueError) as e:
            resp = self._handle_credential_error(e)
            if resp is not None:
                return resp
            raise
        return Response(serializer.data)


class BrandAssetViewSet(viewsets.ModelViewSet):
    """Lista, cria e deleta assets (intro/outro/CTA) por marca."""
    queryset = BrandAsset.objects.all()
    serializer_class = BrandAssetSerializer
    pagination_class = StandardResultsSetPagination
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        qs = super().get_queryset()
        brand = self.request.query_params.get("brand")
        asset_type = self.request.query_params.get("asset_type")
        if brand:
            qs = qs.filter(brand_id=brand)
        if asset_type:
            qs = qs.filter(asset_type=asset_type)
        return qs
