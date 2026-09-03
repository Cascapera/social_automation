"""Jobs de render.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""

import os
from pathlib import Path

from django.http import FileResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.jobs.models import (
    Job,
    RenderOutput,
)
from apps.jobs.services.job_actions import archive_job as do_archive_job
from apps.jobs.services.job_actions import delete_job as do_delete_job
from apps.jobs.services.subtitles import align_edited_to_original_words
from apps.jobs.tasks import burn_subtitles_task, generate_subtitles_task, process_job

from ..pagination import StandardResultsSetPagination
from ..serializers import (
    JobSerializer,
)


class JobViewSet(viewsets.ModelViewSet):
    """Jobs de renderização."""
    queryset = Job.objects.all()
    serializer_class = JobSerializer
    pagination_class = StandardResultsSetPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(user=self.request.user)
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(brand_id=brand)
        archived = self.request.query_params.get("archived")
        if archived is not None:
            qs = qs.filter(archived=archived.lower() in ("1", "true", "yes"))
        return qs

    def perform_create(self, serializer):
        brand_id = self.request.data.get("brand")
        serializer.save(user=self.request.user, brand_id=brand_id or None)

    def destroy(self, request, *args, **kwargs):
        """Deleta o job e o arquivo exportado. Registros de agendamento são preservados."""
        job = self.get_object()
        do_delete_job(job)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        """Arquiva o job e remove o arquivo exportado."""
        job = self.get_object()
        if job.archived:
            return Response({"error": "Job já está arquivado."}, status=status.HTTP_400_BAD_REQUEST)
        do_archive_job(job)
        return Response({"status": "archived", "job_id": job.id})

    @action(detail=False, methods=["post"])
    def upload(self, request):
        """Upload de vídeo pronto. Analisa (duração, formato) e cria Job com output para agendar."""
        import tempfile

        from apps.jobs.services.ffmpeg import ffprobe_video_info

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "Envie o arquivo de vídeo."}, status=status.HTTP_400_BAD_REQUEST)

        name = request.data.get("name", "") or file_obj.name
        format_hint = request.data.get("format")

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
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return Response({"error": f"Não foi possível analisar o vídeo: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            if tmp_path and tmp_path.exists() and not hasattr(file_obj, "temporary_file_path"):
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        width = info["width"]
        height = info["height"]
        if width and height:
            make_vertical = height > width
        else:
            make_vertical = format_hint != "horizontal"
        if format_hint:
            make_vertical = format_hint == "vertical"

        brand_id = request.data.get("brand")
        job = Job.objects.create(
            user=request.user,
            brand_id=brand_id or None,
            name=name or f"Upload {file_obj.name}",
            status="DONE",
            make_vertical=make_vertical,
        )
        RenderOutput.objects.create(job=job, file=file_obj)
        serializer = JobSerializer(job, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        """Baixa o vídeo com o nome do job no arquivo."""
        job = self.get_object()
        try:
            out = job.output
        except Exception:
            return Response(
                {"error": "Vídeo não encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not out or not out.file:
            return Response(
                {"error": "Arquivo de saída não encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        file_path = out.file.path
        if not os.path.exists(file_path):
            return Response(
                {"error": "Arquivo não existe no disco."},
                status=status.HTTP_404_NOT_FOUND,
            )
        safe_name = "".join(c for c in (job.name or f"Job {job.id}") if c not in r'\/:*?"<>|').strip() or f"job_{job.id}"
        if not safe_name.lower().endswith(".mp4"):
            safe_name += ".mp4"
        response = FileResponse(open(file_path, "rb"), as_attachment=True, filename=safe_name)
        return response

    @action(detail=True, methods=["post"], url_path="generate-subtitles")
    def generate_subtitles(self, request, pk=None):
        """Inicia geração de legendas com Whisper."""
        job = self.get_object()
        if job.status != "DONE":
            return Response(
                {"error": "Job precisa estar concluído para gerar legendas."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            out = job.output
        except Exception:
            return Response(
                {"error": "Vídeo não encontrado."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not out or not out.file:
            return Response(
                {"error": "Arquivo de vídeo não encontrado."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if job.subtitle_status == "generating":
            return Response(
                {"error": "Geração de legendas já em andamento."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        generate_subtitles_task.delay(job.id)
        job.subtitle_status = "generating"
        job.subtitle_error = ""
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        return Response({"status": "generating", "job_id": job.id})

    @action(detail=True, methods=["patch"], url_path="subtitles")
    def update_subtitles(self, request, pk=None):
        """Atualiza segmentos e/ou estilo das legendas."""
        job = self.get_object()
        if job.subtitle_status not in ("ready_for_edit", "burned", "error"):
            return Response(
                {"error": "Legendas não estão prontas para edição."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        segments = request.data.get("segments")
        style = request.data.get("style")
        if segments is not None:
            # Preservar/realinhar words para legendas animadas (mesmo após edição)
            existing = job.subtitle_segments or []
            merged = []
            for i, seg in enumerate(segments):
                s = dict(seg)
                edited_text = (s.get("text") or "").strip()
                orig_words = existing[i]["words"] if i < len(existing) else None
                if orig_words and edited_text:
                    aligned = align_edited_to_original_words(edited_text, orig_words)
                    if aligned:
                        s["words"] = aligned
                elif i < len(existing) and existing[i].get("words"):
                    if s.get("text") == existing[i].get("text"):
                        s["words"] = existing[i]["words"]
                merged.append(s)
            job.subtitle_segments = merged
        if style is not None:
            job.subtitle_style = style
        if segments is not None or style is not None:
            job.save(update_fields=["subtitle_segments", "subtitle_style"])
        return Response(JobSerializer(job, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="burn-subtitles")
    def burn_subtitles(self, request, pk=None):
        """Queima legendas no vídeo."""
        job = self.get_object()
        if job.subtitle_status != "ready_for_edit":
            return Response(
                {"error": "Edite e salve as legendas antes de queimar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not job.subtitle_segments:
            return Response(
                {"error": "Nenhum segmento de legenda."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if job.subtitle_status == "burning":
            return Response(
                {"error": "Queima já em andamento."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        burn_subtitles_task.delay(job.id)
        job.subtitle_status = "burning"
        job.subtitle_error = ""
        job.save(update_fields=["subtitle_status", "subtitle_error"])
        return Response({"status": "burning", "job_id": job.id})

    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Enfileira o job para processamento (Celery)."""
        job = self.get_object()
        if job.status not in ("QUEUED", "FAILED", "DONE"):
            return Response(
                {"error": "Job já está em execução ou não pode ser reenfileirado."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not job.job_cuts.exists():
            return Response(
                {"error": "Job precisa de pelo menos 1 corte."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        process_job.delay(job.id)
        job.status = "QUEUED"
        job.save(update_fields=["status"])
        return Response({"status": "queued", "job_id": job.id})
