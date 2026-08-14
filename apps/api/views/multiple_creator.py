"""Multiple-Creator.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""


from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.multiple_creator.models import MultipleCreatorJob

from ..pagination import StandardResultsSetPagination
from ..serializers import (
    MultipleCreatorJobSerializer,
)


class MultipleCreatorViewSet(viewsets.GenericViewSet):
    """Multiple-Creator: cria job + N BrandExecution e orquestra pipeline.

    Fase 4 entregou create+retrieve. Fase 5 ligou a transcribe_task. Fase 6
    completa o ciclo: transcribe -> fanout -> N AutoCutAnalysis filhas. Esta
    classe agora tambem expoe retry granular por brand.
    """

    queryset = MultipleCreatorJob.objects.all().prefetch_related("brand_executions")
    serializer_class = MultipleCreatorJobSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = StandardResultsSetPagination

    def create(self, request, *args, **kwargs):
        from apps.multiple_creator.tasks import multiple_creator_transcribe_task

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = serializer.save()
        multiple_creator_transcribe_task.delay(job.id)
        out = self.get_serializer(job)
        return Response(out.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        job = self.get_object()
        return Response(self.get_serializer(job).data)

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="retry")
    def retry_brand(self, request, pk=None):
        """Retry granular: re-roda uma brand especifica reaproveitando a transcricao."""
        from apps.multiple_creator.models import MultipleCreatorBrandExecution
        from apps.multiple_creator.tasks import _dispatch_brand_execution

        job = self.get_object()
        if not (job.transcript_segments or []):
            return Response(
                {"detail": "Job sem transcricao concluida; nada para retry."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        brand_id = request.query_params.get("brand_id") or request.data.get("brand_id")
        if not brand_id:
            return Response(
                {"detail": "brand_id obrigatorio."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            brand_id = int(brand_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "brand_id invalido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            execution = MultipleCreatorBrandExecution.objects.select_for_update().get(
                job=job, brand_id=brand_id
            )
        except MultipleCreatorBrandExecution.DoesNotExist:
            return Response(
                {"detail": "Brand nao pertence a este job."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if execution.status in ("PENDING", "ANALYZING", "FINALIZING"):
            return Response(
                {"detail": f"Execucao em andamento (status={execution.status}); aguarde."},
                status=status.HTTP_409_CONFLICT,
            )

        execution.status = "PENDING"
        execution.error = ""
        execution.finished_at = None
        execution.started_at = None
        execution.auto_cut_analysis = None
        execution.save(
            update_fields=[
                "status",
                "error",
                "finished_at",
                "started_at",
                "auto_cut_analysis",
                "updated_at",
            ]
        )
        if job.status in ("DONE", "PARTIAL", "ERROR"):
            job.status = "RUNNING_BRANDS"
            job.progress_message = f"Retry brand={brand_id}."
            job.save(update_fields=["status", "progress_message", "updated_at"])

        _dispatch_brand_execution(job, execution)
        return Response(self.get_serializer(job).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel_job(self, request, pk=None):
        """Cancela um job que ainda não terminou."""
        from apps.multiple_creator.models import MultipleCreatorBrandExecution

        job = self.get_object()
        if job.status in ("DONE", "PARTIAL", "ERROR"):
            return Response(
                {"detail": f"Job já está em estado terminal ({job.status})."},
                status=status.HTTP_409_CONFLICT,
            )
        MultipleCreatorBrandExecution.objects.filter(job=job, status="PENDING").update(
            status="ERROR", error="Cancelado pelo usuário."
        )
        job.status = "ERROR"
        job.error = "Cancelado pelo usuário."
        job.save(update_fields=["status", "error", "updated_at"])
        return Response(self.get_serializer(job).data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        """Exclui o job e todos os dados relacionados."""
        job = self.get_object()
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
