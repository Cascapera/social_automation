"""Vídeos de origem e cortes manuais.

Movido de `apps/api/serializers.py` no R-16 — movimentação pura.
"""

from rest_framework import serializers

from apps.cuts.models import Cut
from apps.mediahub.models import SourceVideo


class SourceVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceVideo
        fields = ["id", "brand", "title", "file", "created_at"]
        read_only_fields = ["created_at"]


class CutSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Cut
        fields = ["id", "source", "name", "start_tc", "end_tc", "format", "duration", "file", "file_url", "created_at"]
        read_only_fields = ["created_at", "file"]

    def get_file_url(self, obj):
        if obj.file:
            # Usa URL relativa para funcionar quando acessado de outro PC na rede
            return obj.file.url
        return None


class CutBulkCreateSerializer(serializers.Serializer):
    """Cria múltiplos cortes de uma vez."""
    source = serializers.PrimaryKeyRelatedField(queryset=SourceVideo.objects.all())
    cuts = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        min_length=1,
    )

    def validate_source(self, value):
        request = self.context.get("request")
        if request and request.user and value.user_id and value.user_id != request.user.id:
            raise serializers.ValidationError("Source não pertence ao usuário.")
        return value

    def validate_cuts(self, value):
        for i, c in enumerate(value):
            if "start_tc" not in c or "end_tc" not in c:
                raise serializers.ValidationError(
                    f"Corte {i}: start_tc e end_tc são obrigatórios."
                )
        return value

    def create(self, validated_data):
        source = validated_data["source"]
        cuts_data = validated_data["cuts"]
        created = []
        for c in cuts_data:
            cut = Cut.objects.create(
                source=source,
                brand=source.brand,
                name=c.get("name", ""),
                start_tc=c["start_tc"],
                end_tc=c["end_tc"],
            )
            created.append(cut)
        return created
