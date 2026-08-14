"""Brands, categorias, assets, contas sociais e credenciais.

Movido de `apps/api/serializers.py` no R-16 — movimentação pura.
"""

from django.db import IntegrityError
from django.utils.text import slugify
from rest_framework import serializers

from apps.brands.models import (
    Brand,
    BrandAsset,
    BrandCategory,
    BrandSocialAccount,
    BrandYouTubeCredential,
)
from apps.social.services.secret_crypto import encrypt_secret, is_secret_configured


class BrandCategorySerializer(serializers.ModelSerializer):
    in_use_by_brand_ids = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BrandCategory
        fields = [
            "id",
            "factory",
            "code",
            "label",
            "is_active",
            "in_use_by_brand_ids",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["code", "is_active", "created_at", "updated_at"]

    def get_in_use_by_brand_ids(self, obj):
        return list(
            Brand.objects.filter(factory_id=obj.factory_id, theme_category=obj.code)
            .values_list("id", flat=True)
        )

    def _generate_code(self, factory, label: str) -> str:
        base = slugify(label or "").replace("-", "_").upper()
        if not base:
            raise serializers.ValidationError({"label": "Rótulo inválido para gerar código."})
        candidate = base[:40]
        i = 2
        while BrandCategory.objects.filter(factory=factory, code=candidate).exists():
            suffix = f"_{i}"
            candidate = (base[: 40 - len(suffix)] + suffix)
            i += 1
        return candidate

    def validate(self, attrs):
        factory = attrs.get("factory") or getattr(self.instance, "factory", None)
        label = (attrs.get("label") or "").strip()
        if self.instance is None:
            if not factory:
                raise serializers.ValidationError({"factory": "Factory é obrigatória."})
            if not label:
                raise serializers.ValidationError({"label": "Rótulo é obrigatório."})
        if label and factory:
            qs = BrandCategory.objects.filter(factory=factory, label=label)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"label": "Já existe uma categoria com esse nome nesta factory."})
        return attrs

    def create(self, validated_data):
        factory = validated_data["factory"]
        label = validated_data["label"].strip()
        code = self._generate_code(factory, label)
        validated_data["label"] = label
        validated_data["code"] = code
        validated_data["is_active"] = True
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # code e is_active não são editáveis aqui; is_active muda via action dedicada.
        validated_data.pop("code", None)
        validated_data.pop("is_active", None)
        if "label" in validated_data:
            validated_data["label"] = validated_data["label"].strip()
        return super().update(instance, validated_data)


class BrandSerializer(serializers.ModelSerializer):
    youtube_client_secret = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
    )
    youtube_client_secret_configured = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Brand
        fields = [
            "id",
            "name",
            "slug",
            "factory",
            "theme_category",
            "youtube_made_for_kids",
            "youtube_description_extra",
            "youtube_client_id",
            "youtube_client_secret",
            "youtube_client_secret_configured",
            "youtube_redirect_uri",
            "thumbnail_font",
            "thumbnail_band_color",
            "thumbnail_text_color",
            "thumbnail_effect_color",
            "short_slot_times",
            "long_slot_times",
            "scheduler_timezone",
            "scheduler_enabled",
            "scheduler_paused",
            "base_start_time",
            "base_end_time",
            "start_jitter_minutes",
            "end_jitter_minutes",
            "daily_min_posts",
            "daily_max_posts",
            "daily_min_long_posts",
            "daily_max_long_posts",
            "min_gap_minutes",
            "max_gap_minutes",
            "active_weekdays",
            "vertical_mode",
            "upload_post_tiktok_enabled",
            "upload_post_tiktok_extra_description",
            "upload_post_x_enabled",
            "upload_post_x_extra_description",
            "upload_post_instagram_enabled",
            "upload_post_instagram_extra_description",
            "upload_post_youtube_enabled",
            "long_video_subtitles_enabled",
            "long_video_logo_enabled",
        ]
        extra_kwargs = {"slug": {"required": False}}

    def get_youtube_client_secret_configured(self, obj):
        return is_secret_configured(getattr(obj, "youtube_client_secret", ""))

    def create(self, validated_data):
        if "youtube_client_secret" in validated_data:
            validated_data["youtube_client_secret"] = encrypt_secret(
                validated_data.get("youtube_client_secret", "")
            )
        if not validated_data.get("slug"):
            base_slug = slugify(validated_data["name"]) or "brand"
            slug = base_slug
            i = 2
            while Brand.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{i}"
                i += 1
            validated_data["slug"] = slug
        try:
            return super().create(validated_data)
        except IntegrityError as exc:
            if "brands_brand.slug" in str(exc):
                raise serializers.ValidationError(
                    {"name": "Já existe uma brand com slug semelhante. Tente outro nome."}
                ) from exc
            raise

    def update(self, instance, validated_data):
        if "youtube_client_secret" in validated_data:
            validated_data["youtube_client_secret"] = encrypt_secret(
                validated_data.get("youtube_client_secret", "")
            )
        return super().update(instance, validated_data)

    def validate(self, attrs):
        dmin = attrs.get("daily_min_posts")
        dmax = attrs.get("daily_max_posts")
        if dmin is not None and dmax is not None and dmin > dmax:
            raise serializers.ValidationError(
                {"daily_min_posts": "Não pode ser maior que daily_max_posts."}
            )
        lmin = attrs.get("daily_min_long_posts")
        lmax = attrs.get("daily_max_long_posts")
        if lmin is not None and lmax is not None and lmin > lmax:
            raise serializers.ValidationError(
                {"daily_min_long_posts": "Não pode ser maior que daily_max_long_posts."}
            )
        g_min = attrs.get("min_gap_minutes")
        g_max = attrs.get("max_gap_minutes")
        if g_min is not None and g_max is not None and g_min > g_max:
            raise serializers.ValidationError(
                {"min_gap_minutes": "Não pode ser maior que max_gap_minutes."}
            )

        # theme_category: code precisa existir e estar ativo na factory da brand.
        code = (attrs.get("theme_category") or "").strip()
        factory = attrs.get("factory") or getattr(self.instance, "factory", None)
        if code and factory:
            exists_active = BrandCategory.objects.filter(
                factory=factory, code=code, is_active=True
            ).exists()
            if not exists_active:
                raise serializers.ValidationError(
                    {"theme_category": "Categoria inválida ou inativa para esta factory."}
                )
        return attrs


class BrandAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandAsset
        fields = ["id", "brand", "asset_type", "label", "file"]

    def validate(self, attrs):
        at = attrs.get("asset_type")
        if self.instance is not None and at is None:
            at = self.instance.asset_type
        f = attrs.get("file")
        if at == "OVERLAY_LONG" and f:
            name = (getattr(f, "name", "") or "").lower()
            ext = name.rsplit(".", 1)[-1] if "." in name else ""
            if ext not in ("mp4", "png", "jpg", "jpeg"):
                raise serializers.ValidationError(
                    {"file": "Formato inválido. Use MP4, PNG ou JPG."}
                )
        return attrs


class BrandSocialAccountSerializer(serializers.ModelSerializer):
    """Conta social conectada (sem tokens sensíveis)."""

    class Meta:
        model = BrandSocialAccount
        fields = ["id", "brand", "platform", "channel_id", "account_name", "created_at"]
        read_only_fields = ["id", "brand", "platform", "channel_id", "account_name", "created_at"]


def _needs_reconnection(last_error: str) -> bool:
    """Indica se o erro exige refazer a conexão OAuth."""
    if not last_error:
        return False
    msg = (last_error or "").lower()
    keywords = [
        "invalid_grant",
        "token_expired",
        "unauthorized_client",
        "refresh_token",
        "reconecte",
        "sem refresh_token",
        "sem tokens",
        "credencial ignorada",
        "oauth não configurado",
        "oauth nao configurado",
    ]
    return any(k in msg for k in keywords)


class BrandYouTubeCredentialSerializer(serializers.ModelSerializer):
    client_secret = serializers.CharField(required=False, allow_blank=True, write_only=True)
    client_secret_configured = serializers.SerializerMethodField(read_only=True)
    is_connected = serializers.SerializerMethodField(read_only=True)
    needs_reconnection = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BrandYouTubeCredential
        fields = [
            "id",
            "brand",
            "label",
            "order_index",
            "is_active",
            "is_for_check",
            "client_id",
            "client_secret",
            "client_secret_configured",
            "redirect_uri",
            "channel_id",
            "account_name",
            "quota_exceeded_until",
            "last_error",
            "needs_reconnection",
            "is_connected",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "channel_id",
            "account_name",
            "quota_exceeded_until",
            "last_error",
            "needs_reconnection",
            "is_connected",
            "created_at",
            "updated_at",
        ]

    def get_client_secret_configured(self, obj):
        return is_secret_configured(getattr(obj, "client_secret", ""))

    def get_is_connected(self, obj):
        return bool((obj.refresh_token or "").strip() and (obj.channel_id or "").strip())

    def get_needs_reconnection(self, obj):
        return _needs_reconnection(getattr(obj, "last_error", "") or "")

    def validate_order_index(self, value):
        if value is None:
            return value
        return max(1, int(value))

    def create(self, validated_data):
        if "client_secret" in validated_data:
            validated_data["client_secret"] = encrypt_secret(validated_data.get("client_secret", ""))
        if "is_for_check" not in validated_data:
            validated_data["is_for_check"] = False
        brand = validated_data.get("brand")
        if brand:
            existing_orders = set(
                BrandYouTubeCredential.objects.filter(brand=brand).values_list("order_index", flat=True)
            )
            requested = validated_data.get("order_index") or 0
            order_index = max(1, int(requested)) if requested else None
            if order_index is None:
                order_index = (max(existing_orders) if existing_orders else 0) + 1
            while order_index in existing_orders:
                order_index += 1
            validated_data["order_index"] = order_index
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "client_secret" in validated_data:
            validated_data["client_secret"] = encrypt_secret(validated_data.get("client_secret", ""))
        return super().update(instance, validated_data)
