import io
import os
import re
import zipfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.auto_cuts.tasks import analyze_auto_cuts_task, finalizar_auto_cut_task
from apps.brands.models import (
    Brand,
    BrandAsset,
    BrandSocialAccount,
    BrandYouTubeCredential,
    Factory,
    SearchChannel,
)
from apps.cuts.models import Cut
from apps.jobs.models import (
    FactoryPostingSchedule,
    Job,
    PostedVideoLog,
    RenderOutput,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.jobs.services.job_actions import archive_job as do_archive_job
from apps.jobs.services.job_actions import delete_job as do_delete_job
from apps.jobs.services.subtitles import align_edited_to_original_words
from apps.jobs.tasks import burn_subtitles_task, generate_subtitles_task, process_job
from apps.mediahub.models import SourceVideo
from apps.social.services.youtube_description import build_youtube_description

from .pagination import StandardResultsSetPagination
from .serializers import (
    AutoCutAnalysisSerializer,
    AutoCutCorteSerializer,
    BrandAssetSerializer,
    BrandSerializer,
    BrandSocialAccountSerializer,
    BrandYouTubeCredentialSerializer,
    CutBulkCreateSerializer,
    CutSerializer,
    FactoryPostingScheduleSerializer,
    FactorySerializer,
    JobSerializer,
    PostedVideoLogSerializer,
    ScheduledPostSerializer,
    SearchChannelSerializer,
    SourceVideoSerializer,
    UserRegisterSerializer,
    VideoInventoryItemSerializer,
)

User = get_user_model()

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _delete_auto_cut_job_files(analysis):
    """Remove vídeo original, chunks e arquivos de cortes do job."""
    import shutil

    media_root = Path(settings.MEDIA_ROOT)
    # Arquivos do lote de cortes prontos (vários vídeos)
    try:
        from apps.auto_cuts.models import AutoCutReadyChunk

        for ch in AutoCutReadyChunk.objects.filter(analysis=analysis):
            if ch.file:
                try:
                    fp = Path(ch.file.path) if ch.file.name else None
                except Exception:
                    fp = None
                try:
                    ch.file.delete(save=False)
                except Exception:
                    pass
                if fp and fp.exists():
                    try:
                        fp.unlink()
                    except Exception:
                        pass
    except Exception:
        pass
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


class FactoryViewSet(viewsets.ModelViewSet):
    """CRUD de factories (pool multicanal)."""
    queryset = Factory.objects.all().order_by("name")
    serializer_class = FactorySerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    @action(detail=True, methods=["post"], url_path="trigger-immediate-schedule")
    def trigger_immediate_schedule(self, request, pk=None):
        """
        Dispara o agendamento imediato para a factory.
        Gera agenda para o dia informado (ou dia seguinte se não informado).
        Respeita horários das brands e vídeos disponíveis no banco.
        Body opcional: {"target_date": "YYYY-MM-DD"}
        """
        from datetime import date, timedelta
        from zoneinfo import ZoneInfo

        from django.utils import timezone

        from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory

        factory = self.get_object()
        factory_tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
        now_local = timezone.now().astimezone(factory_tz)
        default_target = now_local.date() + timedelta(days=1)

        target_date = default_target
        brand_id = None
        if request.data and isinstance(request.data, dict):
            raw = (request.data.get("target_date") or "").strip()
            if raw:
                try:
                    parsed = date.fromisoformat(raw)
                    if parsed < now_local.date():
                        return Response(
                            {"error": "Data não pode ser no passado."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    target_date = parsed
                except (ValueError, TypeError):
                    return Response(
                        {"error": "Data inválida. Use formato YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            bid = request.data.get("brand_id")
            if bid is not None:
                try:
                    brand_id = int(bid)
                except (ValueError, TypeError):
                    pass

        if brand_id:
            from apps.brands.models import Brand
            if not Brand.objects.filter(id=brand_id, factory=factory).exists():
                return Response(
                    {"error": "Brand não pertence a esta factory."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            result = generate_daily_schedule_for_factory(
                factory,
                now_utc=timezone.now(),
                target_date=target_date,
                allow_rerun=True,
                brand_id=brand_id,
                enqueue_immediately=True,
            )
            return Response({
                "created": result.get("created", 0),
                "factory_id": factory.id,
                "target_date": str(target_date),
            })
        except Exception as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"], url_path="youtube-check-connect-url")
    def youtube_check_connect_url(self, request, pk=None):
        """Retorna URL para OAuth da API de busca (YOUTUBE_CHECK_*)."""
        from apps.social.services.youtube_oauth import (
            get_check_client_config,
            get_factory_check_authorization_url,
        )

        factory = self.get_object()
        if not get_check_client_config():
            return Response(
                {"error": "YOUTUBE_CHECK_CLIENT_ID e YOUTUBE_CHECK_CLIENT_SECRET devem estar no .env"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            url = get_factory_check_authorization_url(factory.id)
            return Response({"url": url})
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class SearchChannelViewSet(viewsets.ModelViewSet):
    """CRUD de canais de busca (YouTube) por factory."""
    queryset = SearchChannel.objects.all().select_related("factory", "target_brand")
    serializer_class = SearchChannelSerializer
    pagination_class = StandardResultsSetPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        if factory:
            qs = qs.filter(factory_id=factory)
        return qs.order_by("id")

    def perform_create(self, serializer):
        instance = serializer.save()
        _resolve_search_channel(instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        if "youtube_channel_url" in serializer.validated_data:
            _resolve_search_channel(instance)


def _resolve_search_channel(channel: SearchChannel) -> None:
    """Resolve channel_id e channel_title a partir da URL."""
    from apps.auto_cuts.services.youtube_fetch import (
        _get_youtube_client,
        get_channel_info,
        parse_channel_identifier,
        resolve_channel_id,
    )
    youtube = _get_youtube_client()
    if not youtube:
        return
    channel_id_raw, handle = parse_channel_identifier(channel.youtube_channel_url)
    resolved = resolve_channel_id(youtube, channel_id_raw, handle)
    if resolved:
        channel.youtube_channel_id = resolved
        info = get_channel_info(resolved)
        if info:
            channel.channel_title = (info.get("title") or "")[:200]
        channel.last_checked_at = timezone.now()
        channel.save(update_fields=["youtube_channel_id", "channel_title", "last_checked_at", "updated_at"])


class BrandViewSet(viewsets.ModelViewSet):
    """Lista e cria marcas."""
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        if factory:
            qs = qs.filter(factory_id=factory)
        return qs

    @action(detail=True, methods=["post"], url_path="trigger-immediate-schedule")
    def trigger_immediate_schedule(self, request, pk=None):
        """
        Agendamento imediato para uma marca (com ou sem factory).
        Para marcas sem factory, cria uma factory pessoal automaticamente.
        Body: {"target_date": "YYYY-MM-DD"}
        """
        from datetime import date, timedelta
        from zoneinfo import ZoneInfo

        from django.utils import timezone

        from apps.brands.models import Factory
        from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory

        brand = self.get_object()
        tz_name = (brand.factory.timezone if brand.factory else None) or "America/Sao_Paulo"
        tz = ZoneInfo(tz_name)
        now_local = timezone.now().astimezone(tz)
        default_target = now_local.date() + timedelta(days=1)

        target_date = default_target
        if request.data and isinstance(request.data, dict):
            raw = (request.data.get("target_date") or "").strip()
            if raw:
                try:
                    parsed = date.fromisoformat(raw)
                    if parsed < now_local.date():
                        return Response(
                            {"error": "Data não pode ser no passado."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    target_date = parsed
                except (ValueError, TypeError):
                    return Response(
                        {"error": "Data inválida. Use formato YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        factory = brand.factory
        if not factory:
            factory, created = Factory.objects.get_or_create(
                name=f"{brand.name} (pessoal #{brand.id})",
                defaults={
                    "timezone": "America/Sao_Paulo",
                    "is_active": True,
                    "scheduling_paused": True,
                },
            )
            brand.factory = factory
            brand.save(update_fields=["factory"])
            if not created and not factory.scheduling_paused:
                factory.scheduling_paused = True
                factory.save(update_fields=["scheduling_paused"])

        try:
            result = generate_daily_schedule_for_factory(
                factory,
                now_utc=timezone.now(),
                target_date=target_date,
                allow_rerun=True,
                brand_id=brand.id,
                enqueue_immediately=True,
            )
            return Response({
                "created": result.get("created", 0),
                "factory_id": factory.id,
                "target_date": str(target_date),
            })
        except Exception as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
        youtube_credential_id = request.query_params.get("youtube_credential_id")
        youtube_credential = None
        if youtube_credential_id:
            youtube_credential = BrandYouTubeCredential.objects.filter(
                id=youtube_credential_id,
                brand=brand,
            ).first()
            if not youtube_credential:
                return Response(
                    {"error": "Credencial YouTube não encontrada para esta brand"},
                    status=status.HTTP_404_NOT_FOUND,
                )
        try:
            config = get_client_config(brand=brand, youtube_credential=youtube_credential)
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not config:
            return Response(
                {"error": "OAuth não configurado"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        url = get_authorization_url(
            brand.id,
            youtube_credential.id if youtube_credential else None,
        )
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


class BrandYouTubeCredentialViewSet(viewsets.ModelViewSet):
    """Credenciais YouTube por brand para fallback de cota."""
    queryset = BrandYouTubeCredential.objects.all()
    serializer_class = BrandYouTubeCredentialSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset().select_related("brand")
        brand = self.request.query_params.get("brand")
        if brand:
            qs = qs.filter(brand_id=brand)
        return qs.order_by("order_index", "id")

    def _handle_credential_error(self, exc):
        """Evita 500: devolve 400 com mensagem clara para erros de cadastro."""
        if isinstance(exc, IntegrityError):
            msg = str(exc) or "Conflito ao salvar."
            if "uniq_brand_youtube_credential_order" in msg or "order_index" in msg.lower():
                return Response(
                    {"error": "Já existe outra credencial com essa Ordem nesta marca. Use uma ordem diferente."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"error": f"Conflito de dados: {msg[:200]}"}, status=status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, ValueError) and "SOCIAL_ENCRYPTION_KEY" in str(exc):
            return Response(
                {"error": "Chave de criptografia não configurada. Defina SOCIAL_ENCRYPTION_KEY no .env."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except (IntegrityError, ValueError) as e:
            resp = self._handle_credential_error(e)
            if resp is not None:
                return resp
            raise
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_update(serializer)
        except (IntegrityError, ValueError) as e:
            resp = self._handle_credential_error(e)
            if resp is not None:
                return resp
            raise
        return Response(serializer.data)


class BrandAssetViewSet(viewsets.ModelViewSet):
    """Lista, cria e deleta assets (intro/outro/CTA) por marca."""
    queryset = BrandAsset.objects.all()
    serializer_class = BrandAssetSerializer
    pagination_class = StandardResultsSetPagination
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
    pagination_class = StandardResultsSetPagination
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
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(
                Q(job__user=self.request.user)
                | Q(auto_cut_corte__analysis__user=self.request.user)
            )
        factory = self.request.query_params.get("factory")
        if factory:
            qs = qs.filter(
                Q(job__brand__factory_id=factory)
                | Q(auto_cut_corte__analysis__brand__factory_id=factory)
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

    @action(detail=True, methods=["post"], url_path="remove-awaiting")
    def remove_awaiting(self, request, pk=None):
        """
        Remove item aguardando postagem:
        - remove ScheduledPost
        - remove FactoryPostingSchedule vinculado
        - remove VideoInventoryItem do banco
        - remove mídia local do corte (quando existir)
        """
        post = self.get_object()
        if post.status == "DONE":
            return Response(
                {"error": "Não é possível remover um vídeo já postado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        schedule = getattr(post, "factory_schedule", None)
        inventory = getattr(schedule, "inventory_item", None) if schedule else None
        deleted_files = 0
        deleted_thumbnails = 0

        with transaction.atomic():
            # 1) apaga mídia local vinculada ao inventário (quando houver corte)
            if inventory and inventory.auto_cut_corte_id:
                corte = inventory.auto_cut_corte
                if corte and getattr(corte, "file", None):
                    try:
                        corte.file.delete(save=False)
                        deleted_files += 1
                    except Exception:
                        pass
                if corte and getattr(corte, "thumbnail", None):
                    try:
                        corte.thumbnail.delete(save=False)
                        deleted_thumbnails += 1
                    except Exception:
                        pass

            # 2) remove agendamento operacional e inventário
            schedule_id = schedule.id if schedule else None
            inventory_id = inventory.id if inventory else None
            if schedule:
                schedule.delete()
            if inventory:
                inventory.delete()

            # 3) remove ScheduledPost
            post_id = post.id
            post.delete()

        return Response(
            {
                "ok": True,
                "deleted_scheduled_post_id": post_id,
                "deleted_factory_schedule_id": schedule_id,
                "deleted_inventory_item_id": inventory_id,
                "deleted_media_files": deleted_files,
                "deleted_media_thumbnails": deleted_thumbnails,
            }
        )


class VideoInventoryItemViewSet(viewsets.ReadOnlyModelViewSet):
    """Banco de vídeos por brand/factory."""
    pagination_class = StandardResultsSetPagination
    queryset = VideoInventoryItem.objects.all().select_related(
        "brand", "factory",
        "auto_cut_corte",
        "auto_cut_corte__analysis",
        "auto_cut_corte__analysis__source",
    ).prefetch_related(
        "posting_schedules__scheduled_post",
    )
    serializer_class = VideoInventoryItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        brand = self.request.query_params.get("brand")
        status_filter = self.request.query_params.get("status")
        video_type = self.request.query_params.get("video_type")
        if factory:
            qs = qs.filter(factory_id=factory)
        if brand:
            qs = qs.filter(brand_id=brand)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if video_type in ("SHORT", "LONG"):
            qs = qs.filter(video_type=video_type)
        return qs.order_by("-created_at")

    @action(detail=True, methods=["post"], url_path="remove-awaiting")
    def remove_awaiting(self, request, pk=None):
        """
        Remove item aguardando postagem diretamente pelo inventário:
        - remove ScheduledPost vinculado (quando houver)
        - remove FactoryPostingSchedule vinculado (quando houver)
        - remove VideoInventoryItem
        - remove mídia local do corte
        """
        inventory = self.get_object()
        if inventory.status == "POSTED":
            return Response(
                {"error": "Não é possível remover um vídeo já postado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        schedules = list(
            FactoryPostingSchedule.objects.select_related("scheduled_post")
            .filter(inventory_item=inventory)
            .order_by("id")
        )
        scheduled_post_ids = [
            s.scheduled_post_id for s in schedules if getattr(s, "scheduled_post_id", None)
        ]

        deleted_files = 0
        deleted_thumbnails = 0
        with transaction.atomic():
            corte = getattr(inventory, "auto_cut_corte", None)
            if corte and getattr(corte, "file", None):
                try:
                    corte.file.delete(save=False)
                    deleted_files += 1
                except Exception:
                    pass
            if corte and getattr(corte, "thumbnail", None):
                try:
                    corte.thumbnail.delete(save=False)
                    deleted_thumbnails += 1
                except Exception:
                    pass

            if scheduled_post_ids:
                ScheduledPost.objects.filter(id__in=scheduled_post_ids).delete()
            if schedules:
                FactoryPostingSchedule.objects.filter(id__in=[s.id for s in schedules]).delete()

            inventory_id = inventory.id
            inventory.delete()

        return Response(
            {
                "ok": True,
                "deleted_inventory_item_id": inventory_id,
                "deleted_factory_schedule_count": len(schedules),
                "deleted_scheduled_post_count": len(scheduled_post_ids),
                "deleted_media_files": deleted_files,
                "deleted_media_thumbnails": deleted_thumbnails,
            }
        )

    @action(detail=True, methods=["post"], url_path="retry-posting")
    def retry_posting(self, request, pk=None):
        """
        Reativa a postagem para um item aguardando do inventário:
        - ScheduledPost -> PENDING (mantendo horário planejado quando existir)
        - FactoryPostingSchedule -> PLANNED
        - VideoInventoryItem -> SCHEDULED
        - Enfileira tentativa imediata somente se for para agora
        """
        inventory = self.get_object()
        if inventory.status == "POSTED":
            return Response(
                {"error": "Este vídeo já foi postado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        schedule = (
            FactoryPostingSchedule.objects.select_related("scheduled_post")
            .filter(inventory_item=inventory)
            .order_by("-id")
            .first()
        )
        now = timezone.now()
        post = schedule.scheduled_post if schedule and schedule.scheduled_post_id else None
        # Permite reagendar: se o front enviar scheduled_at, usa como próximo horário.
        scheduled_raw = (request.data or {}).get("scheduled_at")
        if scheduled_raw:
            parsed = parse_datetime(str(scheduled_raw))
            if parsed:
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                next_try = parsed if parsed > now else (now + timedelta(seconds=30))
            else:
                planned_slot = (
                    (schedule.scheduled_at if schedule else None)
                    or inventory.scheduled_for
                    or (post.scheduled_at if post else None)
                )
                next_try = planned_slot if planned_slot and planned_slot > now else (now + timedelta(seconds=30))
        else:
            planned_slot = (
                (schedule.scheduled_at if schedule else None)
                or inventory.scheduled_for
                or (post.scheduled_at if post else None)
            )
            # Respeita o horário já planejado; só usa "agora + 30s" se não houver horário válido.
            next_try = planned_slot if planned_slot and planned_slot > now else (now + timedelta(seconds=30))

        # Se não houver ScheduledPost, cria um agendamento imediato para permitir
        # "tentar novamente" direto do banco (status AVAILABLE/SCHEDULED sem post vinculado).
        if post is None:
            corte = getattr(inventory, "auto_cut_corte", None)
            if not corte:
                return Response(
                    {"error": "Item sem corte/mídia vinculada para postagem."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            platform = "YT" if inventory.video_type == "SHORT" else "YTB"
            post = ScheduledPost.objects.create(
                job=None,
                auto_cut_corte=corte,
                platforms=[platform],
                social_account=None,
                scheduled_at=next_try,
                title=(inventory.title or "")[:200],
                description=(inventory.description or ""),
                privacy_status="private",
                status="PENDING",
            )
            if schedule is None:
                schedule = FactoryPostingSchedule.objects.create(
                    factory=inventory.factory,
                    brand=inventory.brand,
                    inventory_item=inventory,
                    video_type=inventory.video_type,
                    scheduled_at=next_try,
                    status="PLANNED",
                    next_retry_at=next_try,
                    scheduled_post=post,
                )
            else:
                schedule.scheduled_post = post
                schedule.scheduled_at = next_try
                schedule.status = "PLANNED"
                schedule.next_retry_at = next_try
                schedule.save(
                    update_fields=["scheduled_post", "scheduled_at", "status", "next_retry_at", "updated_at"]
                )
        elif post.status == "DONE":
            return Response(
                {"error": "Este agendamento já foi concluído."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            post.status = "PENDING"
            post.retry_count = 0
            post.error = ""
            post.posted_at = None
            post.scheduled_at = next_try
            post.save(update_fields=["status", "retry_count", "error", "posted_at", "scheduled_at"])

            schedule.status = "PLANNED"
            schedule.next_retry_at = next_try
            schedule.save(update_fields=["status", "next_retry_at", "updated_at"])

            inventory.status = "SCHEDULED"
            inventory.scheduled_for = next_try
            inventory.last_error = ""
            inventory.save(update_fields=["status", "scheduled_for", "last_error", "updated_at"])

        from apps.social.tasks import post_to_platforms_task

        queued_immediately = next_try <= (now + timedelta(seconds=35))
        if queued_immediately:
            post_to_platforms_task.delay(post.id)

        return Response(
            {
                "ok": True,
                "inventory_item_id": inventory.id,
                "scheduled_post_id": post.id,
                "scheduled_for": next_try,
                "queued_immediately": queued_immediately,
            }
        )

    @action(detail=True, methods=["get"], url_path="download-media")
    def download_media(self, request, pk=None):
        """
        Baixa vídeo (mp4) e thumbnail em um único arquivo ZIP para postagem manual.
        """
        inventory = self.get_object()
        if inventory.status == "POSTED":
            return Response(
                {"error": "Vídeo já postado. Mídias podem ter sido removidas."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        corte = getattr(inventory, "auto_cut_corte", None)
        if not corte:
            return Response(
                {"error": "Item sem corte vinculado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        has_video = corte.file and corte.file.name
        has_thumb = corte.thumbnail and corte.thumbnail.name
        if not has_video and not has_thumb:
            return Response(
                {"error": "Nenhuma mídia disponível para download."},
                status=status.HTTP_404_NOT_FOUND,
            )
        safe_title = "".join(c for c in (inventory.title or f"video_{inventory.id}") if c not in r'\/:*?"<>|').strip() or f"video_{inventory.id}"
        # Descrição igual à usada na postagem (referência do vídeo original + texto extra da brand)
        full_description = build_youtube_description(
            corte=corte,
            brand=getattr(inventory, "brand", None),
            title=inventory.title,
            description_override=inventory.description,
        )
        final_title = (inventory.title or "").strip() or "Vídeo"
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # Txt com título e descrição prontos para copiar e colar no YouTube
            txt_lines = [
                "=== TÍTULO (copie para o campo Título) ===",
                final_title,
                "",
                "=== DESCRIÇÃO (copie e cole no YouTube) ===",
                full_description,
            ]
            zf.writestr(f"{safe_title}_descricao.txt", "\n".join(txt_lines).encode("utf-8"))
            if has_video:
                try:
                    fp = Path(corte.file.path)
                    if fp.exists():
                        ext = fp.suffix.lower() if fp.suffix else ".mp4"
                        zf.write(fp, arcname=f"{safe_title}{ext}")
                except Exception:
                    pass
            if has_thumb:
                try:
                    fp = Path(corte.thumbnail.path)
                    if fp.exists():
                        ext = fp.suffix.lower() if fp.suffix else ".jpg"
                        zf.write(fp, arcname=f"{safe_title}_thumb{ext}")
                except Exception:
                    pass
        zip_buffer.seek(0)
        filename = f"{safe_title}_midias.zip"
        response = FileResponse(zip_buffer, as_attachment=True, filename=filename)
        response["Content-Type"] = "application/zip"
        return response

    @action(detail=True, methods=["post"], url_path="mark-posted")
    def mark_posted(self, request, pk=None):
        """
        Marca item como postado manualmente: move para vídeos postados e remove mídias locais.
        """
        inventory = self.get_object()
        if inventory.status == "POSTED":
            return Response(
                {"error": "Este vídeo já está marcado como postado."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        posted_raw = (request.data or {}).get("posted_at")
        if posted_raw:
            posted_at = parse_datetime(str(posted_raw))
            if not posted_at:
                return Response(
                    {"error": "posted_at inválido."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if timezone.is_naive(posted_at):
                posted_at = timezone.make_aware(posted_at, timezone.get_current_timezone())
        else:
            posted_at = timezone.now()
        schedules = list(
            FactoryPostingSchedule.objects.select_related("scheduled_post")
            .filter(inventory_item=inventory)
            .order_by("id")
        )
        scheduled_post_ids = [s.scheduled_post_id for s in schedules if getattr(s, "scheduled_post_id", None)]
        deleted_files = 0
        deleted_thumbnails = 0
        with transaction.atomic():
            corte = getattr(inventory, "auto_cut_corte", None)
            if corte and getattr(corte, "file", None):
                try:
                    corte.file.delete(save=False)
                    deleted_files += 1
                except Exception:
                    pass
            if corte and getattr(corte, "thumbnail", None):
                try:
                    corte.thumbnail.delete(save=False)
                    deleted_thumbnails += 1
                except Exception:
                    pass
            for post_id in scheduled_post_ids:
                post = ScheduledPost.objects.filter(id=post_id).first()
                if post:
                    post.status = "DONE"
                    post.posted_at = posted_at
                    post.error = ""
                    post.save(update_fields=["status", "posted_at", "error"])
            for s in schedules:
                s.status = "DONE"
                s.save(update_fields=["status", "updated_at"])
            inventory.status = "POSTED"
            inventory.posted_at = posted_at
            inventory.last_error = ""
            inventory.save(update_fields=["status", "posted_at", "last_error", "updated_at"])
            factory = inventory.factory
            brand = inventory.brand
            if factory and brand and not PostedVideoLog.objects.filter(
                inventory_item=inventory,
                external_platform="MANUAL",
                external_video_id="manual",
            ).exists():
                PostedVideoLog.objects.create(
                    factory=factory,
                    brand=brand,
                    inventory_item=inventory,
                    external_platform="MANUAL",
                    external_video_id="manual",
                    posted_at=posted_at,
                    metadata_snapshot={
                        "manual_post": True,
                        "manual_posted_at": posted_at.isoformat(),
                    },
                )
        return Response(
            {
                "ok": True,
                "inventory_item_id": inventory.id,
                "posted_at": posted_at,
                "deleted_media_files": deleted_files,
                "deleted_media_thumbnails": deleted_thumbnails,
            }
        )


class FactoryPostingScheduleViewSet(viewsets.ReadOnlyModelViewSet):
    """Agenda e status operacional para debug."""
    queryset = FactoryPostingSchedule.objects.all().select_related("factory", "brand", "inventory_item", "scheduled_post")
    serializer_class = FactoryPostingScheduleSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        brand = self.request.query_params.get("brand")
        status_filter = self.request.query_params.get("status")
        if factory:
            qs = qs.filter(factory_id=factory)
        if brand:
            qs = qs.filter(brand_id=brand)
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs.order_by("scheduled_at", "id")


class PostedVideoLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Histórico de vídeos já postados por brand."""
    queryset = PostedVideoLog.objects.all().select_related("factory", "brand", "inventory_item")
    serializer_class = PostedVideoLogSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        brand = self.request.query_params.get("brand")
        if factory:
            qs = qs.filter(factory_id=factory)
        if brand:
            qs = qs.filter(brand_id=brand)
        return qs.order_by("-posted_at", "-id")


class AutoCutAnalysisViewSet(viewsets.ModelViewSet):
    """Análise automática de cortes virais."""
    queryset = AutoCutAnalysis.objects.all()
    serializer_class = AutoCutAnalysisSerializer
    pagination_class = StandardResultsSetPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "head", "options", "delete"]

    def get_queryset(self):
        from django.db.models import Q
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            # Inclui jobs do usuário OU do auto-fetch (user=None)
            qs = qs.filter(Q(user=self.request.user) | Q(user__isnull=True))
        brand = self.request.query_params.get("brand")
        factory = self.request.query_params.get("factory")
        if brand:
            qs = qs.filter(brand_id=brand)
        elif factory:
            qs = qs.filter(brand__factory_id=factory)
        # exclude_finalized=1: remove jobs que já têm cortes finalizados (aparecem só em Cortes finalizados)
        if self.request.query_params.get("exclude_finalized") == "1":
            from django.db.models import Exists, OuterRef
            qs = qs.exclude(
                Exists(AutoCutCorte.objects.filter(analysis_id=OuterRef("pk"), is_finalized=True))
            )
        return (
            qs.select_related("target_brand", "brand", "brand__factory")
            .prefetch_related("ready_chunks")
            .order_by("-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        file_obj = request.FILES.get("file")
        source_id = request.data.get("source")
        youtube_url = (request.data.get("youtube_url") or "").strip()
        brand_id = request.data.get("brand")
        target_brand_id = request.data.get("target_brand")
        distribution_mode = (request.data.get("distribution_mode") or "theme").strip().lower()
        if distribution_mode not in ("theme", "distribute"):
            distribution_mode = "theme"
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
        vertical_mode = (request.data.get("vertical_mode") or "zoom_crop").strip().lower()
        if vertical_mode not in ("zoom_crop", "frame_center"):
            vertical_mode = "zoom_crop"
        if prompt_version not in (
            "viral",
            "viral_long",
            "educational",
            "viral_en",
            "viral_long_en",
            "educational_en",
            "viral_translate",
        ):
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

        long_overlay_raw = request.data.get("long_overlay_enabled")
        long_overlay_enabled = str(long_overlay_raw or "").lower() in ("1", "true", "yes", "on")
        long_overlay_asset_val = request.data.get("long_overlay_asset")
        long_overlay_asset_id = None
        if long_overlay_enabled:
            if not long_overlay_asset_val:
                return Response(
                    {"error": "Selecione um overlay ou desative a opção."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                long_overlay_asset_id = int(long_overlay_asset_val)
            except (TypeError, ValueError):
                return Response(
                    {"error": "Overlay inválido."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ovl = BrandAsset.objects.filter(
                id=long_overlay_asset_id,
                brand_id=brand_id,
                asset_type="OVERLAY_LONG",
            ).first()
            if not ovl:
                return Response(
                    {"error": "Overlay inválido para esta brand."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

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

        # Duplicata para a busca automática: ProcessedYoutubeVideo é gravado só após análise
        # manual concluída com sucesso (ver register_manual_youtube_success em analyze_auto_cuts_task).
        # Assim o mesmo URL pode ser reprocessado se o job falhou; a busca automática continua
        # ignorando vídeos já processados com sucesso (manual ou auto).

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
            target_brand_id=target_brand_id or None,
            distribution_mode=distribution_mode,
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
            vertical_mode=vertical_mode,
            long_overlay_enabled=long_overlay_enabled,
            long_overlay_asset_id=long_overlay_asset_id if long_overlay_enabled else None,
        )
        analysis.save()

        analyze_auto_cuts_task.delay(analysis.id)

        serializer = AutoCutAnalysisSerializer(analysis, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="upload-ready-cuts")
    def upload_ready_cuts(self, request):
        """
        Upload de cortes prontos: vários vídeos em um único job (ordem = ordem dos arquivos).
        Campos: files[], brand (obrigatório), name (nome do job, obrigatório),
        transcribe (true/false), create_long_video (true/false), vertical_mode,
        titles_language (pt|en — idioma dos títulos gerados pela LLM).
        """
        files = request.FILES.getlist("files") or request.FILES.getlist("file")
        brand_id = request.data.get("brand") or request.POST.get("brand")
        job_name = (request.data.get("name") or request.POST.get("name") or "").strip()
        vertical_mode = (request.data.get("vertical_mode") or request.POST.get("vertical_mode") or "zoom_crop").strip().lower()
        if vertical_mode not in ("zoom_crop", "frame_center"):
            vertical_mode = "zoom_crop"
        tr_raw = (request.data.get("transcribe") or request.POST.get("transcribe") or "true")
        lr_raw = (request.data.get("create_long_video") or request.POST.get("create_long_video") or "false")
        transcribe = str(tr_raw).lower() in ("1", "true", "yes", "on")
        create_long = str(lr_raw).lower() in ("1", "true", "yes", "on")
        titles_lang_raw = (
            request.data.get("titles_language")
            or request.POST.get("titles_language")
            or "pt"
        )
        titles_language = str(titles_lang_raw).strip().lower()
        if titles_language not in ("pt", "en"):
            titles_language = "pt"
        long_overlay_raw = request.data.get("long_overlay_enabled")
        long_overlay_enabled = str(long_overlay_raw or "").lower() in ("1", "true", "yes", "on")
        long_overlay_asset_val = request.data.get("long_overlay_asset")
        long_overlay_asset_id = None
        if long_overlay_enabled:
            if not long_overlay_asset_val:
                return Response(
                    {"error": "Selecione um overlay ou desative a opção."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                long_overlay_asset_id = int(long_overlay_asset_val)
            except (TypeError, ValueError):
                return Response(
                    {"error": "Overlay inválido."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ovl = BrandAsset.objects.filter(
                id=long_overlay_asset_id,
                brand_id=brand_id,
                asset_type="OVERLAY_LONG",
            ).first()
            if not ovl:
                return Response(
                    {"error": "Overlay inválido para esta brand."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if not brand_id:
            return Response(
                {"error": "Informe brand_id (obrigatório)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not job_name:
            return Response(
                {"error": "Informe o nome do job (name)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not files:
            return Response(
                {"error": "Envie pelo menos um arquivo de vídeo (files ou file)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.auto_cuts.models import AutoCutAnalysis, AutoCutReadyChunk
        from apps.auto_cuts.tasks import analyze_auto_cuts_task

        analysis = AutoCutAnalysis(
            user=request.user,
            brand_id=brand_id,
            target_brand_id=brand_id,
            name=job_name,
            is_ready_cuts=True,
            vertical_mode=vertical_mode,
            ready_cuts_transcribe=transcribe,
            ready_cuts_create_long_video=create_long,
            ready_cuts_long_fade_duration=0.5,
            ready_cuts_titles_language=titles_language,
            long_overlay_enabled=long_overlay_enabled,
            long_overlay_asset_id=long_overlay_asset_id if long_overlay_enabled else None,
        )
        analysis.save()
        for i, file_obj in enumerate(files):
            AutoCutReadyChunk.objects.create(
                analysis=analysis,
                order_index=i,
                file=file_obj,
            )
        analyze_auto_cuts_task.delay(analysis.id)
        data = AutoCutAnalysisSerializer(
            AutoCutAnalysis.objects.prefetch_related("ready_chunks").get(pk=analysis.pk),
            context={"request": request},
        ).data
        return Response(data, status=status.HTTP_201_CREATED)

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
        vertical_mode = data.get("vertical_mode") or "zoom_crop"
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
        lo_en_raw = data.get("long_overlay_enabled")
        if lo_en_raw is None:
            long_overlay_enabled = bool(getattr(analysis, "long_overlay_enabled", False))
        else:
            long_overlay_enabled = str(lo_en_raw).lower() in ("1", "true", "yes", "on")
        lo_id_raw = data.get("long_overlay_asset_id")
        if lo_id_raw is None:
            long_overlay_asset_id = getattr(analysis, "long_overlay_asset_id", None)
        else:
            try:
                long_overlay_asset_id = int(lo_id_raw) if lo_id_raw else None
            except (TypeError, ValueError):
                long_overlay_asset_id = None
        if long_overlay_enabled and not long_overlay_asset_id:
            return Response(
                {"error": "Selecione um overlay ou desative a opção."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if long_overlay_enabled and long_overlay_asset_id:
            bid = getattr(analysis, "brand_id", None)
            if not bid or not BrandAsset.objects.filter(
                id=long_overlay_asset_id,
                brand_id=bid,
                asset_type="OVERLAY_LONG",
            ).exists():
                return Response(
                    {"error": "Overlay inválido para esta brand."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
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
            long_overlay_enabled=long_overlay_enabled,
            long_overlay_asset_id=long_overlay_asset_id if long_overlay_enabled else None,
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

        # Janela efetiva = agora para o próximo ciclo do Beat enfileirar (mantém validação de start/end acima)
        now = timezone.now()
        start_at = now
        end_at = now

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
            for corte, schedule_dt in zip(cortes, times, strict=True):
                exists = ScheduledPost.objects.filter(
                    auto_cut_corte=corte,
                    status__in=["PENDING", "POSTING", "DONE"],
                ).exists()
                if exists:
                    skipped += 1
                    continue
                ScheduledPost.objects.create(
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
    pagination_class = StandardResultsSetPagination
    http_method_names = ["get", "patch", "delete", "post", "head", "options"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        from django.db.models import Q
        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            qs = qs.filter(Q(analysis__user=self.request.user) | Q(analysis__user__isnull=True))
        brand = self.request.query_params.get("brand")
        factory = self.request.query_params.get("factory")
        if brand:
            qs = qs.filter(analysis__brand_id=brand)
        elif factory:
            qs = qs.filter(analysis__brand__factory_id=factory)
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
            safe_title = str(title).strip()[:200]
            corte.suggestion.title = safe_title
            corte.suggestion.save(update_fields=["title"])
            # Mantém título alinhado no banco de vídeos e em agendamentos ainda não postados
            VideoInventoryItem.objects.filter(auto_cut_corte=corte).update(title=safe_title[:220])
            ScheduledPost.objects.filter(auto_cut_corte=corte).exclude(status="DONE").update(
                title=safe_title[:200]
            )
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
        if scheduled_raw:
            parsed = parse_datetime(str(scheduled_raw))
            if not parsed:
                return Response(
                    {"error": "scheduled_at inválido."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())

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

        # Enfileira no próximo ciclo do Beat (scheduled_at <= now)
        scheduled_at = timezone.now()

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


def _parse_positive_int(val):
    if val is None or val == "":
        return None
    try:
        i = int(val)
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


class DashboardMetricsView(APIView):
    """
    Métricas reais do dashboard (AutoCut): vídeos processados, minutos, cortes finalizados.
    Escopo: ?brand=ID ou ?factory=ID (obrigatório um deles). Com ambos, valida que a brand
    pertence à factory e aplica escopo pela brand.
    """

    def get(self, request):
        from .dashboard_metrics import compute_dashboard_metrics

        brand_id = _parse_positive_int(request.query_params.get("brand"))
        factory_id = _parse_positive_int(request.query_params.get("factory"))
        if not brand_id and not factory_id:
            return Response(
                {"detail": "Informe o parâmetro brand ou factory."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if brand_id and factory_id:
            if not Brand.objects.filter(pk=brand_id, factory_id=factory_id).exists():
                return Response(
                    {"detail": "Brand não pertence à factory indicada."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            factory_id = None

        data = compute_dashboard_metrics(request.user, brand_id, factory_id)
        return Response(data)


class FactoryYoutubeDashboardView(APIView):
    """
    YouTube via Upload Post: resumo agregado por factory (assinantes, views no período, série, top vídeos).
    GET /api/dashboard/factory/<factory_id>/youtube-summary/?period=last_month&refresh=1
    """

    def get(self, request, factory_id=None):
        from django.core.cache import cache

        from .factory_youtube_dashboard import (
            build_factory_youtube_dashboard,
            normalize_period_param,
        )

        fid = _parse_positive_int(factory_id)
        if not fid:
            return Response({"detail": "factory_id inválido."}, status=status.HTTP_400_BAD_REQUEST)
        if not Factory.objects.filter(pk=fid).exists():
            return Response({"detail": "Factory não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        period = normalize_period_param(request.query_params.get("period"))
        if request.query_params.get("refresh") in ("1", "true", "yes"):
            cache.delete(f"factory_youtube_dash:{fid}:{period}")

        data = build_factory_youtube_dashboard(fid, period=period)
        return Response(data)
