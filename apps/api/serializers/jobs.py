"""Jobs de render.

Movido de `apps/api/serializers.py` no R-16 — movimentação pura.
"""

from rest_framework import serializers

from apps.cuts.models import Cut
from apps.jobs.models import (
    Job,
    JobCut,
    RenderOutput,
)


class JobCutInlineSerializer(serializers.ModelSerializer):
    cut_id = serializers.PrimaryKeyRelatedField(
        queryset=Cut.objects.all(), source="cut"
    )

    class Meta:
        model = JobCut
        fields = ["cut_id"]

    def to_internal_value(self, data):
        if isinstance(data, int):
            return {"cut_id": data}
        return super().to_internal_value(data)


class JobSerializer(serializers.ModelSerializer):
    cut_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=True,
    )
    output_url = serializers.SerializerMethodField(read_only=True)
    scheduled_summary = serializers.SerializerMethodField(read_only=True)
    can_delete = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Job
        fields = [
            "id",
            "name",
            "archived",
            "cut_ids",
            "target_platforms",
            "make_vertical",
            "intro_asset",
            "outro_asset",
            "transition",
            "transition_duration",
            "status",
            "progress",
            "output_url",
            "error",
            "created_at",
            "started_at",
            "finished_at",
            "scheduled_summary",
            "can_delete",
            "subtitle_status",
            "subtitle_segments",
            "subtitle_style",
            "subtitle_error",
        ]
        read_only_fields = [
            "status", "progress", "error", "archived",
            "created_at", "started_at", "finished_at",
        ]

    def get_output_url(self, obj):
        try:
            out = obj.output
            if out and out.file:
                return out.file.url
        except (RenderOutput.DoesNotExist, AttributeError):
            pass
        return None

    def get_scheduled_summary(self, obj):
        posts = obj.scheduled_posts.all()
        if not posts:
            return None
        done = sum(1 for p in posts if p.status == "DONE")
        pending = sum(1 for p in posts if p.status in ("PENDING", "POSTING"))
        return {"total": len(posts), "posted": done, "pending": pending}

    def get_can_delete(self, obj):
        return True

    def create(self, validated_data):
        cut_ids = validated_data.pop("cut_ids")
        request = self.context.get("request")
        if request and request.user:
            validated_data["user"] = request.user

        job = Job.objects.create(**validated_data)
        for order, cut_id in enumerate(cut_ids):
            JobCut.objects.create(job=job, cut_id=cut_id, order=order)
        return job


class JobRunSerializer(serializers.Serializer):
    """Apenas para validação do endpoint run."""
    pass
