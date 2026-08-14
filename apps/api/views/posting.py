"""Publicação: agendamentos, banco de vídeos e histórico.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""

import io
import logging
import zipfile
from pathlib import Path

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.jobs.logging_utils import log_event
from apps.jobs.models import (
    FactoryPostingSchedule,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.jobs.services.inventory_actions import (
    InventoryActionError,
    remove_awaiting_item,
    retry_posting_item,
)
from apps.jobs.services.media_cleanup import delete_file_field
from apps.social.services.posting_state import mark_item_posted
from apps.social.services.youtube_description import build_youtube_description

from ..pagination import StandardResultsSetPagination
from ..serializers import (
    FactoryPostingScheduleSerializer,
    PostedVideoLogSerializer,
    ScheduledPostSerializer,
    VideoInventoryItemSerializer,
)

logger = logging.getLogger(__name__)


def _add_media_to_zip(zf, campo, *, arcname: str, ext_padrao: str, operation: str, **contexto) -> bool:
    """Adiciona um arquivo de mídia ao ZIP. Devolve `False` se não deu (R-20 / D-10).

    Duas formas de não dar, e as duas eram silenciosas antes: o arquivo não existe no disco
    (banco e disco discordando) ou a leitura falha. Nenhuma das duas levanta — o pacote sai
    sem o arquivo —, mas agora as duas viram evento no log e aparecem para o usuário.
    """
    try:
        fp = Path(campo.path)
        if not fp.exists():
            log_event(
                logger,
                event="download_media_missing",
                status="error",
                error="arquivo não encontrado no disco",
                operation=operation,
                file_name=str(fp),
                **contexto,
            )
            return False
        ext = fp.suffix.lower() if fp.suffix else ext_padrao
        zf.write(fp, arcname=arcname.format(ext=ext))
        return True
    except Exception as e:
        log_event(
            logger,
            event="download_media_failed",
            status="error",
            error=str(e),
            operation=operation,
            **contexto,
        )
        return False


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
                if corte:
                    deleted_files += int(
                        delete_file_field(
                            getattr(corte, "file", None),
                            operation="delete_scheduled_post",
                            inventory_item_id=inventory.id,
                            corte_id=corte.id,
                        )
                    )
                    deleted_thumbnails += int(
                        delete_file_field(
                            getattr(corte, "thumbnail", None),
                            operation="delete_scheduled_post",
                            inventory_item_id=inventory.id,
                            corte_id=corte.id,
                        )
                    )

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

    AWAITING_STATUSES = ("AVAILABLE", "SCHEDULED", "POSTING", "FAILED")

    def get_queryset(self):
        qs = super().get_queryset()
        factory = self.request.query_params.get("factory")
        brand = self.request.query_params.get("brand")
        status_filter = self.request.query_params.get("status")
        video_type = self.request.query_params.get("video_type")
        bucket = (self.request.query_params.get("bucket") or "").strip().lower()
        if factory:
            qs = qs.filter(factory_id=factory)
        if brand:
            qs = qs.filter(brand_id=brand)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if video_type in ("SHORT", "LONG"):
            qs = qs.filter(video_type=video_type)
        if bucket == "awaiting":
            qs = qs.filter(status__in=self.AWAITING_STATUSES)
        elif bucket == "posted":
            qs = qs.filter(status="POSTED")
        return qs.order_by("-created_at")

    @action(detail=True, methods=["post"], url_path="remove-awaiting")
    def remove_awaiting(self, request, pk=None):
        """Remove item aguardando postagem, com agendamento, post e mídia (R-14)."""
        try:
            return Response(remove_awaiting_item(self.get_object()))
        except InventoryActionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="retry-posting")
    def retry_posting(self, request, pk=None):
        """Reativa a postagem de um item aguardando do inventário (R-14)."""
        try:
            payload = retry_posting_item(
                self.get_object(),
                scheduled_at_raw=(request.data or {}).get("scheduled_at"),
            )
        except InventoryActionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)

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
        brand = getattr(inventory, "brand", None)
        brand_slug = slugify(getattr(brand, "slug", "") or getattr(brand, "name", "")) or "brand"
        file_base_name = f"{brand_slug}-{inventory.id}"
        safe_title = "".join(c for c in (inventory.title or f"video_{inventory.id}") if c not in r'\/:*?"<>|').strip() or f"video_{inventory.id}"
        # Descrição igual à usada na postagem (referência do vídeo original + texto extra da brand)
        full_description = build_youtube_description(
            corte=corte,
            brand=brand,
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
            # O banco diz que a mídia existe; o disco é quem decide. Quando os dois
            # discordam, o pacote sai incompleto — e antes do R-20 saía calado (D-10).
            faltando = []
            if has_video and not _add_media_to_zip(
                zf,
                corte.file,
                arcname=f"{safe_title}{{ext}}",
                ext_padrao=".mp4",
                operation="download_media_video",
                inventory_item_id=inventory.id,
                corte_id=corte.id,
            ):
                faltando.append("vídeo")
            if has_thumb and not _add_media_to_zip(
                zf,
                corte.thumbnail,
                arcname=f"{safe_title}_thumb{{ext}}",
                ext_padrao=".jpg",
                operation="download_media_thumbnail",
                inventory_item_id=inventory.id,
                corte_id=corte.id,
            ):
                faltando.append("thumbnail")

            if faltando:
                zf.writestr(
                    "ATENCAO_arquivos_faltando.txt",
                    (
                        "Este pacote saiu INCOMPLETO.\n\n"
                        f"Não foi possível incluir: {', '.join(faltando)}.\n\n"
                        "O item existe no banco, mas o arquivo não foi encontrado ou não pôde "
                        "ser lido do disco do servidor. Avise o suporte com o número do item: "
                        f"{inventory.id}.\n"
                    ).encode(),
                )

        # Pacote só com o texto não serve para postar: é melhor o usuário saber agora do que
        # descobrir ao abrir o ZIP.
        if (has_video or has_thumb) and len(faltando) == (int(bool(has_video)) + int(bool(has_thumb))):
            return Response(
                {
                    "error": (
                        "A mídia deste item não está mais disponível no servidor. "
                        "Nada foi incluído no pacote."
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        zip_buffer.seek(0)
        filename = f"{file_base_name}.zip"
        response = FileResponse(zip_buffer, as_attachment=True, filename=filename)
        response["Content-Type"] = "application/zip"
        if faltando:
            # Cabeçalho para o front poder avisar sem precisar abrir o ZIP.
            response["X-Missing-Media"] = ",".join(faltando)
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
        deleted_files = 0
        deleted_thumbnails = 0
        with transaction.atomic():
            corte = getattr(inventory, "auto_cut_corte", None)
            if corte:
                deleted_files += int(
                    delete_file_field(
                        getattr(corte, "file", None),
                        operation="mark_posted",
                        inventory_item_id=inventory.id,
                        corte_id=corte.id,
                    )
                )
                deleted_thumbnails += int(
                    delete_file_field(
                        getattr(corte, "thumbnail", None),
                        operation="mark_posted",
                        inventory_item_id=inventory.id,
                        corte_id=corte.id,
                    )
                )
            # Cópia D do D-02: a transição dos 4 modelos era escrita aqui dentro, num
            # handler HTTP. Agora é do posting_state, o dono único (R-07). A remoção de
            # mídia continua sendo da view — é efeito da ação, não da máquina de estados.
            mark_item_posted(
                inventory,
                platform="MANUAL",
                external_video_id="manual",
                posted_at=posted_at,
                log_metadata={
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
