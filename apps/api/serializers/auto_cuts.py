"""Cortes automáticos: análises, sugestões e cortes.

Movido de `apps/api/serializers.py` no R-16 — movimentação pura.
"""

from rest_framework import serializers

from apps.auto_cuts.models import (
    AutoCutAnalysis,
    AutoCutCorte,
    AutoCutReadyChunk,
    AutoCutSuggestion,
)


class AutoCutSuggestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutoCutSuggestion
        fields = [
            "id",
            "cut_type",
            "start_tc",
            "end_tc",
            "title",
            "reason",
            "hook",
            "virality_score",
            "theme_category",
            "source_asset_id",
            "rank",
            "duration_seconds",
            "duration_minutes",
        ]


class AutoCutCorteSerializer(serializers.ModelSerializer):
    suggestion = AutoCutSuggestionSerializer(read_only=True)
    file_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    thumbnail = serializers.ImageField(write_only=True, required=False, allow_null=True)
    analysis_id = serializers.IntegerField(read_only=True)
    analysis_name = serializers.CharField(source="analysis.name", read_only=True)

    class Meta:
        model = AutoCutCorte
        fields = [
            "id",
            "analysis_id",
            "analysis_name",
            "suggestion",
            "file_url",
            "thumbnail",
            "thumbnail_url",
            "format",
            "needs_subtitle",
            "user_wants_finalize",
            "is_finalized",
            "subtitle_segments",
            "created_at",
        ]

    def get_file_url(self, obj):
        if obj.file:
            return obj.file.url
        return None

    def get_thumbnail_url(self, obj):
        if obj.thumbnail:
            return obj.thumbnail.url
        return None

    def validate_thumbnail(self, value):
        if not value:
            return value
        max_size = 2 * 1024 * 1024  # 2MB (limite do YouTube)
        if getattr(value, "size", 0) > max_size:
            raise serializers.ValidationError("Thumbnail deve ter no máximo 2MB.")
        content_type = (getattr(value, "content_type", "") or "").lower()
        allowed = {"image/jpeg", "image/jpg", "image/png", "image/gif"}
        if content_type and content_type not in allowed:
            raise serializers.ValidationError("Formato inválido. Use JPG, PNG ou GIF.")
        return value


class AutoCutReadyChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutoCutReadyChunk
        fields = ["id", "order_index", "duration_seconds"]


class AutoCutAnalysisSerializer(serializers.ModelSerializer):
    suggestions = AutoCutSuggestionSerializer(many=True, read_only=True)
    cortes = AutoCutCorteSerializer(many=True, read_only=True)
    ready_chunks = AutoCutReadyChunkSerializer(many=True, read_only=True)
    target_brand_name = serializers.SerializerMethodField(read_only=True)
    factory_name = serializers.SerializerMethodField(read_only=True)

    def get_target_brand_name(self, obj):
        target = getattr(obj, "target_brand", None)
        if target:
            return getattr(target, "name", None)
        if (getattr(obj, "distribution_mode", "") or "").strip() == "distribute":
            return "Distribuir pelas Brands"
        return "Por tema"

    def get_factory_name(self, obj):
        brand = getattr(obj, "brand", None)
        if not brand:
            return None
        factory = getattr(brand, "factory", None)
        return getattr(factory, "name", None) if factory else None

    class Meta:
        model = AutoCutAnalysis
        fields = [
            "id",
            "brand",
            "name",
            "target_brand_name",
            "distribution_mode",
            "factory_name",
            "assunto",
            "convidados",
            "prompt_version",
            "thumbnail_font",
            "thumbnail_band_color",
            "thumbnail_text_color",
            "thumbnail_stroke_color",
            "shorts_target",
            "longs_target",
            "youtube_url",
            "status",
            "progress",
            "progress_message",
            "transcript",
            "error",
            "created_at",
            "is_ready_cuts",
            "vertical_mode",
            "ready_cuts_transcribe",
            "ready_cuts_create_long_video",
            "ready_cuts_long_fade_duration",
            "ready_cuts_titles_language",
            "long_overlay_enabled",
            "long_overlay_asset",
            "thumb_template_short",
            "thumb_template_long",
            "suggestions",
            "cortes",
            "ready_chunks",
        ]
        read_only_fields = ["status", "progress", "progress_message", "transcript", "error", "created_at"]
