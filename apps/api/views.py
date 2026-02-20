import os
from django.contrib.auth import get_user_model
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import AllowAny
from django.http import FileResponse

from apps.brands.models import Brand, BrandAsset
from apps.mediahub.models import SourceVideo
from apps.cuts.models import Cut
from apps.jobs.models import Job, RenderOutput, ScheduledPost
from apps.jobs.tasks import process_job, generate_subtitles_task, burn_subtitles_task
from apps.jobs.services.subtitles import align_edited_to_original_words
from apps.jobs.services.job_actions import archive_job as do_archive_job, delete_job as do_delete_job

from .serializers import (
    BrandSerializer,
    BrandAssetSerializer,
    SourceVideoSerializer,
    CutSerializer,
    CutBulkCreateSerializer,
    JobSerializer,
    ScheduledPostSerializer,
    UserRegisterSerializer,
)

User = get_user_model()


class RegisterViewSet(viewsets.ViewSet):
    """Registro de novo usuário."""
    permission_classes = [AllowAny]
    serializer_class = UserRegisterSerializer

    def create(self, request):
        serializer = UserRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {"id": user.id, "username": user.username, "email": getattr(user, "email", "")},
            status=status.HTTP_201_CREATED,
        )


class BrandViewSet(viewsets.ModelViewSet):
    """Lista e cria marcas."""
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    http_method_names = ["get", "post", "head", "options"]


class BrandAssetViewSet(viewsets.ModelViewSet):
    """Lista, cria e deleta assets (intro/outro/CTA) por marca."""
    queryset = BrandAsset.objects.all()
    serializer_class = BrandAssetSerializer
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        qs = super().get_queryset()
        brand = self.request.query_params.get("brand")
        asset_type = self.request.query_params.get("asset_type")
        if brand:
            qs = qs.filter(brand_id=brand)
        if asset_type:
            qs = qs.filter(asset_type=asset_type)
        return qs


class SourceVideoViewSet(viewsets.ModelViewSet):
    """Upload e gestão de vídeos fonte."""
    queryset = SourceVideo.objects.all()
    serializer_class = SourceVideoSerializer
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
        from apps.cuts.models import Cut
        from .serializers import CutSerializer

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
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        from django.db.models import Q

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
        from pathlib import Path
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


class JobViewSet(viewsets.ModelViewSet):
    """Jobs de renderização."""
    queryset = Job.objects.all()
    serializer_class = JobSerializer
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
        """Deleta o job e o arquivo exportado. Só permite se não houver agendamento pendente."""
        job = self.get_object()
        try:
            do_delete_job(job)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
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
        from pathlib import Path
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


class ScheduledPostViewSet(viewsets.ModelViewSet):
    """Agendamento de postagens."""
    queryset = ScheduledPost.objects.all()
    serializer_class = ScheduledPostSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(job__user=self.request.user)
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(job__brand_id=brand)
        return qs
