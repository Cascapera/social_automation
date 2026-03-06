import os
import re
from pathlib import Path
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import AllowAny
from django.http import FileResponse

from apps.brands.models import Brand, BrandAsset, BrandSocialAccount
from apps.mediahub.models import SourceVideo
from apps.cuts.models import Cut
from apps.jobs.models import Job, RenderOutput, ScheduledPost
from apps.jobs.tasks import process_job, generate_subtitles_task, burn_subtitles_task
from apps.auto_cuts.models import AutoCutAnalysis, AutoCutSuggestion, AutoCutCorte
from apps.auto_cuts.tasks import analyze_auto_cuts_task, finalizar_auto_cut_task
from django.conf import settings
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
    AutoCutAnalysisSerializer,
    AutoCutSuggestionSerializer,
    AutoCutCorteSerializer,
    BrandSocialAccountSerializer,
)

User = get_user_model()

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _delete_auto_cut_job_files(analysis):
    """Remove vídeo original, chunks e arquivos de cortes do job."""
    import shutil

    media_root = Path(settings.MEDIA_ROOT)
    # Vídeo original (upload)
    if analysis.file:
        try:
            fp = Path(analysis.file.path) if analysis.file.name else None
        except Exception:
            fp = None
        try:
            analysis.file.delete(save=False)
        except Exception:
            pass
        if fp and fp.exists():
            try:
                fp.unlink()
            except Exception:
                pass
    # Cortes: deleta via Django e também por path/glob (evita arquivos órfãos)
    cortes_dir = media_root / "auto_cuts" / "cortes"
    for corte in AutoCutCorte.objects.filter(analysis=analysis):
        if corte.file:
            try:
                fp = Path(corte.file.path) if corte.file.name else None
            except Exception:
                fp = None
            try:
                corte.file.delete(save=False)
            except Exception:
                pass
            if fp and fp.exists():
                try:
                    fp.unlink()
                except Exception:
                    pass
        if corte.thumbnail:
            try:
                tfp = Path(corte.thumbnail.path) if corte.thumbnail.name else None
            except Exception:
                tfp = None
            try:
                corte.thumbnail.delete(save=False)
            except Exception:
                pass
            if tfp and tfp.exists():
                try:
                    tfp.unlink()
                except Exception:
                    pass
    # Remove por padrão job_X_sug_Y (caso path diverga ou delete do Django falhe)
    if cortes_dir.exists():
        try:
            for f in cortes_dir.glob(f"job_{analysis.id}_sug_*.mp4"):
                f.unlink()
        except Exception:
            pass
    # Chunks em processamento (cortes_processo)
    chunks_dir = media_root / "cortes_processo" / str(analysis.id)
    if chunks_dir.exists():
        try:
            shutil.rmtree(chunks_dir)
        except Exception:
            pass


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
    http_method_names = ["get", "post", "patch", "head", "options"]

    @action(detail=True, methods=["get"])
    def social_accounts(self, request, pk=None):
        """Lista contas sociais conectadas à marca."""
        brand = self.get_object()
        accounts = BrandSocialAccount.objects.filter(brand=brand)
        serializer = BrandSocialAccountSerializer(accounts, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def youtube_connect_url(self, request, pk=None):
        """Retorna URL para iniciar OAuth YouTube (frontend redireciona)."""
        from apps.social.services.youtube_oauth import get_authorization_url, get_client_config

        brand = self.get_object()
        if not get_client_config():
            return Response(
                {"error": "OAuth não configurado"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        url = get_authorization_url(brand.id)
        return Response({"url": url})

    @action(detail=True, methods=["patch"], url_path="youtube-description")
    def youtube_description(self, request, pk=None):
        """Atualiza configurações de descrição/infantil do YouTube por marca."""
        brand = self.get_object()
        data = request.data or {}
        fields_to_update = []
        if "youtube_description_extra" in data:
            brand.youtube_description_extra = str(data.get("youtube_description_extra") or "")
            fields_to_update.append("youtube_description_extra")
        if "youtube_made_for_kids" in data:
            raw = data.get("youtube_made_for_kids")
            brand.youtube_made_for_kids = str(raw).lower() in ("1", "true", "yes", "on")
            fields_to_update.append("youtube_made_for_kids")
        if fields_to_update:
            brand.save(update_fields=fields_to_update)
        return Response(
            {
                "id": brand.id,
                "youtube_description_extra": brand.youtube_description_extra,
                "youtube_made_for_kids": brand.youtube_made_for_kids,
            }
        )


class BrandSocialAccountViewSet(viewsets.ModelViewSet):
    """Lista e remove contas sociais. Filtro: ?brand=X"""
    queryset = BrandSocialAccount.objects.all()
    serializer_class = BrandSocialAccountSerializer
    http_method_names = ["get", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(brand_id=brand)
        return qs


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
            qs = qs.filter(
                Q(job__user=self.request.user)
                | Q(auto_cut_corte__analysis__user=self.request.user)
            )
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(
                Q(job__brand_id=brand)
                | Q(auto_cut_corte__analysis__brand_id=brand)
            )
        return qs

    @action(detail=True, methods=["post"], url_path="reschedule")
    def reschedule(self, request, pk=None):
        """Reagenda postagem com falha para nova data/hora e retorna para PENDING."""
        post = self.get_object()
        if post.status != "FAILED":
            return Response(
                {"error": "Apenas agendamentos com status FAILED podem ser reagendados."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        scheduled_raw = (request.data or {}).get("scheduled_at")
        if not scheduled_raw:
            return Response(
                {"error": "scheduled_at é obrigatório."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        scheduled_at = parse_datetime(str(scheduled_raw))
        if not scheduled_at:
            return Response(
                {"error": "scheduled_at inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if timezone.is_naive(scheduled_at):
            scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())

        post.scheduled_at = scheduled_at
        post.status = "PENDING"
        post.error = ""
        post.retry_count = 0
        post.posted_at = None
        post.save(update_fields=["scheduled_at", "status", "error", "retry_count", "posted_at"])
        serializer = self.get_serializer(post)
        return Response(serializer.data)


class AutoCutAnalysisViewSet(viewsets.ModelViewSet):
    """Análise automática de cortes virais."""
    queryset = AutoCutAnalysis.objects.all()
    serializer_class = AutoCutAnalysisSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "head", "options", "delete"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(user=self.request.user)
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(brand_id=brand)
        # exclude_finalized=1: remove jobs que já têm cortes finalizados (aparecem só em Cortes finalizados)
        if self.request.query_params.get("exclude_finalized") == "1":
            from django.db.models import Exists, OuterRef
            qs = qs.exclude(
                Exists(AutoCutCorte.objects.filter(analysis_id=OuterRef("pk"), is_finalized=True))
            )
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        file_obj = request.FILES.get("file")
        source_id = request.data.get("source")
        youtube_url = (request.data.get("youtube_url") or "").strip()
        brand_id = request.data.get("brand")
        name = request.data.get("name", "")
        assunto = request.data.get("assunto", "")
        convidados = request.data.get("convidados", "")
        prompt_version = (request.data.get("prompt_version") or "viral").strip().lower()
        thumbnail_font = (request.data.get("thumbnail_font") or "impact").strip().lower()
        thumbnail_band_color = (request.data.get("thumbnail_band_color") or "#E12E20").strip()
        thumbnail_text_color = (request.data.get("thumbnail_text_color") or "#0A0A0A").strip()
        thumbnail_stroke_color = (request.data.get("thumbnail_stroke_color") or "#FFEBDC").strip()
        shorts_target_raw = request.data.get("shorts_target", 12)
        longs_target_raw = request.data.get("longs_target", 3)
        if prompt_version not in ("viral", "educational", "viral_en", "educational_en"):
            prompt_version = "viral"
        if thumbnail_font not in ("anton", "bebas", "montserrat", "impact"):
            thumbnail_font = "impact"
        if not HEX_COLOR_RE.match(thumbnail_band_color):
            thumbnail_band_color = "#E12E20"
        if not HEX_COLOR_RE.match(thumbnail_text_color):
            thumbnail_text_color = "#0A0A0A"
        if not HEX_COLOR_RE.match(thumbnail_stroke_color):
            thumbnail_stroke_color = "#FFEBDC"
        try:
            shorts_target = int(shorts_target_raw)
        except (TypeError, ValueError):
            shorts_target = 12
        try:
            longs_target = int(longs_target_raw)
        except (TypeError, ValueError):
            longs_target = 3
        shorts_target = max(1, min(30, shorts_target))
        longs_target = max(1, min(10, longs_target))

        sources_count = sum([bool(file_obj), bool(source_id), bool(youtube_url)])
        if sources_count == 0:
            return Response(
                {"error": "Envie um arquivo de vídeo (file), selecione um source ou informe um URL do YouTube."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sources_count > 1:
            return Response(
                {"error": "Use apenas uma opção: file, source ou youtube_url."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Nome do job: usuário informou, nome do vídeo, ou "Job N"
        if name and name.strip():
            job_name = name.strip()
        elif file_obj:
            job_name = Path(file_obj.name).stem if hasattr(file_obj, "name") else "Vídeo"
        elif source_id:
            job_name = f"Source #{source_id}"
        elif youtube_url:
            job_name = "YouTube"
        else:
            n = AutoCutAnalysis.objects.filter(brand_id=brand_id or None).count() + 1
            job_name = f"Job {n}"

        analysis = AutoCutAnalysis(
            user=request.user,
            brand_id=brand_id or None,
            source_id=source_id or None,
            file=file_obj if file_obj else None,
            youtube_url=youtube_url or "",
            name=job_name,
            assunto=assunto or "",
            convidados=convidados or "",
            prompt_version=prompt_version,
            thumbnail_font=thumbnail_font,
            thumbnail_band_color=thumbnail_band_color.upper(),
            thumbnail_text_color=thumbnail_text_color.upper(),
            thumbnail_stroke_color=thumbnail_stroke_color.upper(),
            shorts_target=shorts_target,
            longs_target=longs_target,
        )
        analysis.save()

        analyze_auto_cuts_task.delay(analysis.id)

        serializer = AutoCutAnalysisSerializer(analysis, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="reset-stuck")
    def reset_stuck(self, request):
        """Marca análises travadas (pending/transcribing/analyzing) como erro."""
        qs = self.get_queryset()
        stuck = qs.filter(status__in=["pending", "transcribing", "analyzing"])
        count = stuck.update(status="error", error="Cancelado (travado)")
        return Response({"reset": count})

    @action(detail=False, methods=["post"], url_path="delete-stuck")
    def delete_stuck(self, request):
        """Deleta jobs interrompidos (pending, transcribing, analyzing, error) e seus arquivos."""
        qs = self.get_queryset()
        to_delete = list(qs.filter(status__in=["pending", "transcribing", "analyzing", "error"]))
        for analysis in to_delete:
            _delete_auto_cut_job_files(analysis)
            analysis.delete()
        return Response({"deleted": len(to_delete)})

    def destroy(self, request, *args, **kwargs):
        """Deleta job e tudo: vídeo original, chunks, cortes e arquivos."""
        analysis = self.get_object()
        _delete_auto_cut_job_files(analysis)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="finalizar")
    def finalizar(self, request, pk=None):
        """Finaliza cortes marcados (reenquadra vertical, queima legenda se needs_subtitle)."""
        analysis = self.get_object()
        if analysis.user_id != request.user.id:
            return Response({"error": "Não autorizado."}, status=status.HTTP_403_FORBIDDEN)
        data = request.data or {}
        subtitle_style = data.get("subtitle_style") or {}
        vertical_mode = data.get("vertical_mode") or "frame_center"
        background_color = data.get("background_color") or "#000000"
        custom_text = data.get("custom_text") or ""
        font_size_title = data.get("font_size_title")
        font_size_text = data.get("font_size_text")
        title_color = data.get("title_color")
        text_color = data.get("text_color")
        horizontal_insert_logo = data.get("horizontal_insert_logo", False)
        horizontal_logo_x = data.get("horizontal_logo_x")
        horizontal_logo_y = data.get("horizontal_logo_y")
        overlay_animation_asset_id = data.get("overlay_animation_asset_id")
        overlay_position = data.get("overlay_position") or "bottom_right"
        overlay_margin = data.get("overlay_margin")
        overlay_height = data.get("overlay_height")
        finalizar_auto_cut_task.delay(
            analysis.id,
            subtitle_style=subtitle_style,
            vertical_mode=vertical_mode,
            background_color=background_color,
            custom_text=custom_text,
            font_size_title=font_size_title,
            font_size_text=font_size_text,
            title_color=title_color,
            text_color=text_color,
            horizontal_insert_logo=horizontal_insert_logo,
            horizontal_logo_x=horizontal_logo_x,
            horizontal_logo_y=horizontal_logo_y,
            overlay_animation_asset_id=overlay_animation_asset_id,
            overlay_position=overlay_position,
            overlay_margin=overlay_margin,
            overlay_height=overlay_height,
        )
        return Response({"finalized": "Em processamento (reenquadramento e legendas em background)"})

    @action(detail=True, methods=["post"], url_path="bulk-schedule")
    def bulk_schedule(self, request, pk=None):
        """
        Agenda em massa os cortes finalizados deste job em uma janela [start_at, end_at].
        Curtos -> plataforma YT; longos -> plataforma YTB.
        """
        analysis = self.get_object()
        if analysis.user_id != request.user.id:
            return Response({"error": "Não autorizado."}, status=status.HTTP_403_FORBIDDEN)

        start_raw = (request.data or {}).get("start_at")
        end_raw = (request.data or {}).get("end_at")
        social_account_raw = (request.data or {}).get("social_account")
        privacy_status = (request.data or {}).get("privacy_status") or "private"
        if privacy_status not in ("public", "private", "unlisted"):
            privacy_status = "private"

        start_at = parse_datetime(start_raw or "")
        end_at = parse_datetime(end_raw or "")
        if not start_at or not end_at:
            return Response(
                {"error": "start_at e end_at devem ser datetime válidos."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if timezone.is_naive(start_at):
            start_at = timezone.make_aware(start_at, timezone.get_current_timezone())
        if timezone.is_naive(end_at):
            end_at = timezone.make_aware(end_at, timezone.get_current_timezone())
        if end_at < start_at:
            return Response(
                {"error": "end_at deve ser maior ou igual a start_at."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        selected_social_account = None
        if social_account_raw not in (None, "", "null"):
            try:
                social_account_id = int(social_account_raw)
            except (TypeError, ValueError):
                return Response(
                    {"error": "social_account inválido."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            selected_social_account = BrandSocialAccount.objects.filter(
                id=social_account_id,
                brand_id=analysis.brand_id,
                platform__in=["YT", "YTB"],
            ).first()
            if not selected_social_account:
                return Response(
                    {"error": "Conta social inválida para esta marca."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        finalized_qs = analysis.cortes.filter(is_finalized=True).select_related("suggestion")
        short_cortes = [c for c in finalized_qs if (getattr(c.suggestion, "cut_type", "") == "short")]
        long_cortes = [c for c in finalized_qs if (getattr(c.suggestion, "cut_type", "") == "long")]

        def build_times(start_dt, end_dt, count):
            if count <= 0:
                return []
            if count == 1:
                return [start_dt]
            total_seconds = (end_dt - start_dt).total_seconds()
            step = total_seconds / (count - 1)
            return [start_dt + timedelta(seconds=round(step * i)) for i in range(count)]

        created = 0
        skipped = 0
        from apps.jobs.models import ScheduledPost

        for cortes, platform in ((short_cortes, "YT"), (long_cortes, "YTB")):
            times = build_times(start_at, end_at, len(cortes))
            for corte, schedule_dt in zip(cortes, times):
                exists = ScheduledPost.objects.filter(
                    auto_cut_corte=corte,
                    status__in=["PENDING", "POSTING", "DONE"],
                ).exists()
                if exists:
                    skipped += 1
                    continue
                post = ScheduledPost.objects.create(
                    job=None,
                    auto_cut_corte=corte,
                    platforms=[platform],
                    social_account=selected_social_account,
                    scheduled_at=schedule_dt,
                    title=(corte.suggestion.title or "")[:200] if corte.suggestion_id else "",
                    privacy_status=privacy_status,
                )
                created += 1

        return Response(
            {
                "created": created,
                "skipped": skipped,
                "short_count": len(short_cortes),
                "long_count": len(long_cortes),
                "window_start": start_at,
                "window_end": end_at,
            }
        )


class AutoCutSuggestionViewSet(viewsets.ViewSet):
    """Ações sobre sugestões de corte."""

    def destroy(self, request, pk=None):
        """Remove uma sugestão."""
        try:
            suggestion = AutoCutSuggestion.objects.get(pk=pk)
        except AutoCutSuggestion.DoesNotExist:
            return Response({"error": "Sugestão não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        analysis = suggestion.analysis
        if request.user.is_authenticated and analysis.user_id != request.user.id:
            return Response({"error": "Não autorizado."}, status=status.HTTP_403_FORBIDDEN)
        suggestion.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="create-cut")
    def create_cut(self, request, pk=None):
        """Placeholder: prepara criação do corte (não executa ainda)."""
        try:
            suggestion = AutoCutSuggestion.objects.get(pk=pk)
        except AutoCutSuggestion.DoesNotExist:
            return Response({"error": "Sugestão não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        analysis = suggestion.analysis
        if request.user.is_authenticated and analysis.user_id != request.user.id:
            return Response({"error": "Não autorizado."}, status=status.HTTP_403_FORBIDDEN)
        return Response(
            {
                "message": "Gerar corte ainda não implementado. Use start_tc e end_tc para criar o corte manualmente.",
                "suggestion_id": suggestion.id,
                "start_tc": suggestion.start_tc,
                "end_tc": suggestion.end_tc,
                "title": suggestion.title,
            },
            status=status.HTTP_200_OK,
        )


class AutoCutCorteViewSet(viewsets.ModelViewSet):
    """Cortes do auto-cuts. Lista finalizados (tabela) e permite atualizar/deletar."""
    queryset = AutoCutCorte.objects.all()
    serializer_class = AutoCutCorteSerializer
    http_method_names = ["get", "patch", "delete", "post", "head", "options"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(analysis__user=self.request.user)
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(analysis__brand_id=brand)
        # Tabela de finalizados: ?finalized=1
        if self.request.query_params.get("finalized") == "1":
            qs = qs.filter(is_finalized=True)
        # Filtros: date_from, date_to, duration_min, format
        date_from = self.request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        fmt = self.request.query_params.get("format")
        if fmt in ("vertical", "horizontal"):
            qs = qs.filter(format=fmt)
        return qs.select_related("suggestion", "analysis").order_by("-created_at")

    def partial_update(self, request, *args, **kwargs):
        """Atualiza corte. Aceita title e thumbnail para publicação no YouTube."""
        corte = self.get_object()
        old_thumb_path = None
        if corte.thumbnail:
            try:
                old_thumb_path = Path(corte.thumbnail.path) if corte.thumbnail.name else None
            except Exception:
                old_thumb_path = None
        title = request.data.get("title")
        if title is not None and corte.suggestion_id:
            corte.suggestion.title = str(title)[:200]
            corte.suggestion.save(update_fields=["title"])
        response = super().partial_update(request, *args, **kwargs)
        if "thumbnail" in request.FILES and old_thumb_path:
            try:
                new_name = self.get_object().thumbnail.name if self.get_object().thumbnail else ""
            except Exception:
                new_name = ""
            if old_thumb_path.exists() and old_thumb_path.name not in new_name:
                try:
                    old_thumb_path.unlink()
                except Exception:
                    pass
        return response

    def destroy(self, request, *args, **kwargs):
        """Deleta corte e arquivo de vídeo (se existir). Remove registro mesmo sem arquivo."""
        corte = self.get_object()
        if corte.file:
            file_path = None
            try:
                if corte.file.name:
                    file_path = Path(corte.file.path)
            except Exception:
                pass
            try:
                corte.file.delete(save=False)
            except Exception:
                pass
            if file_path and file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass
        if corte.thumbnail:
            thumb_path = None
            try:
                if corte.thumbnail.name:
                    thumb_path = Path(corte.thumbnail.path)
            except Exception:
                pass
            try:
                corte.thumbnail.delete(save=False)
            except Exception:
                pass
            if thumb_path and thumb_path.exists():
                try:
                    thumb_path.unlink()
                except Exception:
                    pass
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="schedule")
    def schedule(self, request, pk=None):
        """
        Agenda um corte finalizado individualmente.
        Plataforma padrão: short -> YT, long -> YTB.
        """
        corte = self.get_object()
        if corte.analysis.user_id != request.user.id:
            return Response({"error": "Não autorizado."}, status=status.HTTP_403_FORBIDDEN)
        if not corte.is_finalized:
            return Response(
                {"error": "Somente cortes finalizados podem ser agendados."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scheduled_raw = (request.data or {}).get("scheduled_at")
        privacy_status = (request.data or {}).get("privacy_status") or "private"
        if privacy_status not in ("public", "private", "unlisted"):
            privacy_status = "private"
        scheduled_at = parse_datetime(scheduled_raw or "")
        if not scheduled_at:
            return Response(
                {"error": "scheduled_at inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if timezone.is_naive(scheduled_at):
            scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())

        cut_type = getattr(corte.suggestion, "cut_type", "")
        platform = "YT" if cut_type == "short" else "YTB"

        existing_posts = ScheduledPost.objects.filter(
            auto_cut_corte=corte,
            status__in=["PENDING", "POSTING", "DONE"],
        ).only("platforms")
        exists = any(platform in (p.platforms or []) for p in existing_posts)
        if exists:
            return Response(
                {"created": False, "skipped": True, "message": "Este corte já possui agendamento para essa plataforma."}
            )

        post = ScheduledPost.objects.create(
            job=None,
            auto_cut_corte=corte,
            platforms=[platform],
            scheduled_at=scheduled_at,
            title=(corte.suggestion.title or "")[:200] if corte.suggestion_id else "",
            privacy_status=privacy_status,
        )
        return Response(
            {
                "created": True,
                "scheduled_post_id": post.id,
                "platform": platform,
                "scheduled_at": scheduled_at,
            },
            status=status.HTTP_201_CREATED,
        )
