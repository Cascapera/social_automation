"""Multiple-Creator.

Movido de `apps/api/serializers.py` no R-16 — movimentação pura.
"""

from rest_framework import serializers

from apps.brands.models import (
    Brand,
)
from apps.mediahub.models import SourceVideo
from apps.multiple_creator.models import (
    MultipleCreatorBrandExecution,
    MultipleCreatorJob,
)


class MultipleCreatorBrandExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MultipleCreatorBrandExecution
        fields = [
            "id",
            "brand",
            "status",
            "auto_cut_analysis",
            "error",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MultipleCreatorJobSerializer(serializers.ModelSerializer):
    brand_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        allow_empty=False,
        min_length=1,
        help_text="Lista de brand IDs para gerar cortes em paralelo.",
    )
    brand_executions = MultipleCreatorBrandExecutionSerializer(many=True, read_only=True)
    file = serializers.FileField(required=False, allow_null=True, write_only=True)
    file_url = serializers.SerializerMethodField(read_only=True)
    source = serializers.PrimaryKeyRelatedField(
        queryset=SourceVideo.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = MultipleCreatorJob
        fields = [
            "id",
            "name",
            "source_kind",
            "source",
            "file",
            "file_url",
            "youtube_url",
            "assunto",
            "convidados",
            "prompt_version",
            "vertical_mode",
            "shorts_target",
            "longs_target",
            "thumbnail_font",
            "thumbnail_band_color",
            "thumbnail_text_color",
            "thumbnail_stroke_color",
            "status",
            "progress",
            "progress_message",
            "error",
            "correlation_id",
            "created_at",
            "updated_at",
            "brand_ids",
            "brand_executions",
        ]
        read_only_fields = [
            "id",
            "source_kind",
            "file_url",
            "status",
            "progress",
            "progress_message",
            "error",
            "correlation_id",
            "created_at",
            "updated_at",
            "brand_executions",
        ]

    def get_file_url(self, obj):
        if not obj.file:
            return None
        try:
            return obj.file.url
        except ValueError:
            return None

    def validate_brand_ids(self, value):
        unique = list(dict.fromkeys(value))
        if not unique:
            raise serializers.ValidationError("Selecione ao menos uma brand.")
        existing = set(Brand.objects.filter(id__in=unique).values_list("id", flat=True))
        missing = [bid for bid in unique if bid not in existing]
        if missing:
            raise serializers.ValidationError(
                f"Brand(s) inexistente(s): {', '.join(str(m) for m in missing)}"
            )
        return unique

    def validate(self, attrs):
        file_obj = attrs.get("file")
        source = attrs.get("source")
        youtube_url = (attrs.get("youtube_url") or "").strip()
        provided = sum(1 for x in (file_obj, source, youtube_url) if x)
        if provided == 0:
            raise serializers.ValidationError(
                "Informe exatamente uma origem: arquivo, source ou URL do YouTube."
            )
        if provided > 1:
            raise serializers.ValidationError(
                "Escolha apenas uma origem (arquivo, source OU URL do YouTube)."
            )
        if file_obj:
            attrs["source_kind"] = "FILE"
        elif source:
            attrs["source_kind"] = "SOURCE"
        else:
            attrs["source_kind"] = "YOUTUBE"
            attrs["youtube_url"] = youtube_url
        return attrs

    def create(self, validated_data):
        brand_ids = validated_data.pop("brand_ids")
        request = self.context.get("request")
        user = request.user if request and request.user and request.user.is_authenticated else None
        job = MultipleCreatorJob.objects.create(user=user, **validated_data)
        MultipleCreatorBrandExecution.objects.bulk_create(
            [MultipleCreatorBrandExecution(job=job, brand_id=bid) for bid in brand_ids]
        )
        return job
