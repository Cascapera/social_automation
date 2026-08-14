"""Factories e canais de busca.

Movido de `apps/api/serializers.py` no R-16 — movimentação pura.
"""

from rest_framework import serializers

from apps.brands.models import (
    Factory,
    SearchChannel,
)


class FactorySerializer(serializers.ModelSerializer):
    has_youtube_check_credential = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Factory
        fields = [
            "id",
            "name",
            "timezone",
            "daily_schedule_start_time",
            "is_active",
            "scheduling_paused",
            "processing_paused",
            "auto_fetch_enabled",
            "auto_fetch_min_per_brand",
            "auto_fetch_min_total",
            "auto_fetch_max_total",
            "auto_fetch_min_video_age_hours",
            "auto_fetch_max_video_age_hours",
            "auto_fetch_prompt_version",
            "auto_fetch_shorts_target",
            "auto_fetch_longs_target",
            "auto_fetch_min_duration_minutes",
            "auto_fetch_min_views",
            "send_thumbnail",
            "has_youtube_check_credential",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "has_youtube_check_credential"]

    def get_has_youtube_check_credential(self, obj):
        from apps.brands.models import FactoryYouTubeCheckCredential
        return FactoryYouTubeCheckCredential.objects.filter(
            factory=obj,
        ).exclude(refresh_token="").exists()


class SearchChannelSerializer(serializers.ModelSerializer):
    target_brand_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = SearchChannel
        fields = [
            "id",
            "factory",
            "youtube_channel_url",
            "youtube_channel_id",
            "channel_title",
            "target_brand",
            "target_brand_name",
            "distribute_by_brands",
            "is_active",
            "last_checked_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["youtube_channel_id", "channel_title", "last_checked_at", "created_at", "updated_at"]

    def get_target_brand_name(self, obj):
        if getattr(obj, "distribute_by_brands", False):
            return "Distribuir pelas Brands"
        return getattr(obj.target_brand, "name", None) if obj.target_brand else "Por tema"
