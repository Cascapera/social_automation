"""Publicação: agendamentos, banco de vídeos e histórico.

Movido de `apps/api/serializers.py` no R-16 — movimentação pura.
"""

from django.utils import timezone
from rest_framework import serializers

from apps.jobs.models import (
    FactoryPostingSchedule,
    Job,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)


class ScheduledPostSerializer(serializers.ModelSerializer):
    job = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all(), required=False, allow_null=True)
    job_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ScheduledPost
        fields = [
            "id",
            "job",
            "job_name",
            "auto_cut_corte",
            "platforms",
            "social_account",
            "scheduled_at",
            "title",
            "description",
            "tags",
            "privacy_status",
            "status",
            "error",
            "created_at",
            "posted_at",
        ]
        read_only_fields = ["status", "error", "created_at", "posted_at"]

    def get_job_name(self, obj):
        if obj.job_id:
            return obj.job.name or f"Job #{obj.job.id}"
        if obj.auto_cut_corte_id:
            suggestion = getattr(obj.auto_cut_corte, "suggestion", None)
            if suggestion and suggestion.title:
                return suggestion.title
            return f"Corte #{obj.auto_cut_corte_id}"
        return "-"

    def create(self, validated_data):
        # Fila de publicação: próximo ciclo do Beat (check_scheduled_posts_task) — não esperar horário futuro
        validated_data["scheduled_at"] = timezone.now()
        return super().create(validated_data)


class VideoInventoryItemSerializer(serializers.ModelSerializer):
    source_display_name = serializers.SerializerMethodField(read_only=True)
    status_message = serializers.SerializerMethodField(read_only=True)
    scheduled_post_id = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = VideoInventoryItem
        fields = [
            "id",
            "factory",
            "brand",
            "auto_cut_corte",
            "video_type",
            "title",
            "description",
            "virality_score",
            "source_asset_id",
            "source_display_name",
            "source_metadata",
            "status",
            "status_message",
            "scheduled_post_id",
            "scheduled_for",
            "posted_at",
            "attempt_count",
            "last_error",
            "created_at",
            "updated_at",
        ]

    def _latest_posting_schedule(self, obj):
        prefetched = getattr(obj, "_prefetched_objects_cache", {}) or {}
        schedules = prefetched.get("posting_schedules")
        if schedules is not None:
            latest = None
            for schedule in schedules:
                if latest is None or getattr(schedule, "id", 0) > getattr(latest, "id", 0):
                    latest = schedule
            return latest
        try:
            return obj.posting_schedules.select_related("scheduled_post").order_by("-id").first()
        except Exception:
            return None

    def get_status_message(self, obj):
        """
        Mensagem de detalhe para status Postando: "Na fila" ou "Aguardando confirmação".
        """
        if obj.status in ("SCHEDULED", "POSTING"):
            schedule = self._latest_posting_schedule(obj)
            post = getattr(schedule, "scheduled_post", None) if schedule else None
            external_ids = getattr(post, "external_ids", None) or {}
            has_yt_id = bool(
                str(external_ids.get("YT") or external_ids.get("YTB") or "").strip()
            )
            if obj.status == "POSTING":
                return "Enviando..."
            return "Aguardando confirmação" if has_yt_id else "Na fila"
        return None

    def get_scheduled_post_id(self, obj):
        schedule = self._latest_posting_schedule(obj)
        return getattr(schedule, "scheduled_post_id", None) if schedule else None

    def get_source_display_name(self, obj):
        """Nome do vídeo original (o mesmo que aparece nos jobs) para exibir na coluna Nome da fonte."""
        corte = getattr(obj, "auto_cut_corte", None)
        if not corte:
            return (obj.source_asset_id or "").strip() or "-"
        analysis = getattr(corte, "analysis", None)
        if not analysis:
            return (obj.source_asset_id or "").strip() or "-"
        # Prioridade: analysis.name (nome do job) > source.title > filename > source_asset_id
        name = (getattr(analysis, "name", None) or "").strip()
        if name:
            return name
        if getattr(analysis, "source_id", None) and getattr(analysis, "source", None):
            title = (getattr(analysis.source, "title", None) or "").strip()
            if title:
                return title
        f = getattr(analysis, "file", None)
        if f and getattr(f, "name", None):
            stem = f.name.rsplit(".", 1)[0] if "." in f.name else f.name
            return stem or f.name
        return (obj.source_asset_id or "").strip() or "-"


class FactoryPostingScheduleSerializer(serializers.ModelSerializer):
    posted_at = serializers.SerializerMethodField()
    posted_on_channel = serializers.SerializerMethodField()
    external_video_id = serializers.SerializerMethodField()

    class Meta:
        model = FactoryPostingSchedule
        fields = [
            "id",
            "factory",
            "brand",
            "inventory_item",
            "video_type",
            "scheduled_at",
            "status",
            "attempt_count",
            "next_retry_at",
            "scheduled_post",
            "daily_plan_item",
            "posted_at",
            "posted_on_channel",
            "external_video_id",
            "created_at",
            "updated_at",
        ]

    def get_posted_at(self, obj):
        post = getattr(obj, "scheduled_post", None)
        if post and getattr(post, "posted_at", None):
            return post.posted_at
        item = getattr(obj, "inventory_item", None)
        return getattr(item, "posted_at", None)

    def get_posted_on_channel(self, obj):
        if obj.status != "DONE":
            return False
        return self.get_posted_at(obj) is not None

    def get_external_video_id(self, obj):
        post = getattr(obj, "scheduled_post", None)
        if not post:
            return ""
        external_ids = getattr(post, "external_ids", {}) or {}
        for value in external_ids.values():
            if value:
                return str(value)
        return ""


class PostedVideoLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostedVideoLog
        fields = [
            "id",
            "factory",
            "brand",
            "inventory_item",
            "external_platform",
            "external_video_id",
            "posted_at",
            "metadata_snapshot",
            "created_at",
        ]
