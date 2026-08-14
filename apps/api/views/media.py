"""Vídeos de origem e cortes manuais.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""

from pathlib import Path

from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.cuts.models import Cut
from apps.mediahub.models import SourceVideo

from ..pagination import StandardResultsSetPagination
from ..serializers import (
    CutBulkCreateSerializer,
    CutSerializer,
    SourceVideoSerializer,
)


class SourceVideoViewSet(viewsets.ModelViewSet):
    """Upload e gestão de vídeos fonte."""
    queryset = SourceVideo.objects.all()
    serializer_class = SourceVideoSerializer
    pagination_class = StandardResultsSetPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(user=self.request.user)
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(brand_id=brand)
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def extract_cuts(self, request, pk=None):
        """Extrai cortes do source, salva como arquivos, deleta o source."""
        from apps.cuts.services import extract_cuts_from_source

        from ..serializers import CutSerializer

        source = self.get_object()
        cuts_data = request.data.get("cuts", [])
        if not cuts_data:
            return Response(
                {"error": "Envie cuts: [{name, start_tc, end_tc, format?}]"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        for i, c in enumerate(cuts_data):
            if "start_tc" not in c or "end_tc" not in c:
                return Response(
                    {"error": f"Corte {i}: start_tc e end_tc obrigatórios"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            created = extract_cuts_from_source(source.id, cuts_data)
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        serializer = CutSerializer(created, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CutViewSet(viewsets.ModelViewSet):
    """Cortes de vídeo."""
    queryset = Cut.objects.all()
    serializer_class = CutSerializer
    pagination_class = StandardResultsSetPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):

        qs = super().get_queryset()
        source = self.request.query_params.get("source")
        if source:
            qs = qs.filter(source_id=source)
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(Q(source__brand_id=brand) | Q(brand_id=brand))
        if self.request.user.is_authenticated:
            qs = qs.filter(Q(user=self.request.user) | Q(source__user=self.request.user))
        return qs

    def destroy(self, request, *args, **kwargs):
        """Deleta o corte e o arquivo. Bloqueia se o corte estiver em algum job."""
        cut = self.get_object()
        if cut.job_cuts.exists():
            return Response(
                {"error": "Não é possível deletar: este corte está em uso em um ou mais jobs."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if cut.file:
            cut.file.delete(save=False)
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["post"])
    def upload(self, request):
        """Upload de corte pronto. Analisa vídeo (duração, formato) e salva."""
        import tempfile

        from apps.jobs.services.ffmpeg import ffprobe_video_info, seconds_to_tc

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "Envie o arquivo de vídeo."}, status=status.HTTP_400_BAD_REQUEST)

        name = request.data.get("name", "") or file_obj.name
        format_hint = request.data.get("format")  # "vertical" ou "horizontal", opcional

        tmp_path = None
        try:
            if hasattr(file_obj, "temporary_file_path"):
                tmp_path = Path(file_obj.temporary_file_path())
            else:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                    for chunk in file_obj.chunks():
                        f.write(chunk)
                    tmp_path = Path(f.name)
                file_obj.seek(0)
            info = ffprobe_video_info(tmp_path)
        except Exception as e:
            if tmp_path and tmp_path.exists() and not hasattr(file_obj, "temporary_file_path"):
                tmp_path.unlink(missing_ok=True)
            return Response({"error": f"Não foi possível analisar o vídeo: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            if tmp_path and tmp_path.exists() and not hasattr(file_obj, "temporary_file_path"):
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        duration = info["duration"]
        width = info["width"]
        height = info["height"]

        if width and height:
            is_vertical = height > width
        else:
            is_vertical = format_hint != "horizontal"

        if format_hint:
            is_vertical = format_hint == "vertical"

        brand_id = request.data.get("brand")
        cut = Cut.objects.create(
            user=request.user,
            source=None,
            brand_id=brand_id or None,
            name=name,
            start_tc="00:00:00",
            end_tc=seconds_to_tc(duration),
            format="vertical" if is_vertical else "horizontal",
            duration=duration,
            file=file_obj,
        )
        serializer = CutSerializer(cut, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def create(self, request, *args, **kwargs):
        """Suporta criação em lote via cuts=[{...}, {...}]."""
        if "cuts" in request.data and "source" in request.data:
            serializer = CutBulkCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            created = serializer.save()
            return Response(
                CutSerializer(created, many=True).data,
                status=status.HTTP_201_CREATED,
            )
        return super().create(request, *args, **kwargs)
