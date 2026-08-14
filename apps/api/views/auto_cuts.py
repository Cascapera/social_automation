"""Cortes automáticos: análises, sugestões e cortes.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""

import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.auto_cuts.tasks import analyze_auto_cuts_task, finalizar_auto_cut_task
from apps.brands.models import (
    BrandAsset,
    BrandSocialAccount,
)
from apps.jobs.models import (
    ScheduledPost,
    VideoInventoryItem,
)

from ..pagination import StandardResultsSetPagination
from ..serializers import (
    AutoCutAnalysisSerializer,
    AutoCutCorteSerializer,
)

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


class AutoCutAnalysisViewSet(viewsets.ModelViewSet):
    """Análise automática de cortes virais."""
    queryset = AutoCutAnalysis.objects.all()
    serializer_class = AutoCutAnalysisSerializer
    pagination_class = StandardResultsSetPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "head", "options", "delete"]

    def get_queryset(self):
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
