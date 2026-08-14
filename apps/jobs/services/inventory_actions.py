"""Ações sobre o banco de vídeos: remover item aguardando e reativar postagem.

Vieram de dentro dos handlers HTTP em `apps/api/views.py` (refactor.md R-14, D-04). Eram
regra de negócio e transação misturadas com view: a mesma ação não podia ser chamada de um
management command ou de uma task sem duplicar o código.

Nada aqui sabe o que é HTTP. O erro de regra sai como `InventoryActionError`, e é a view
que decide virar 400 com a mensagem no corpo — as mensagens são contrato de tela e estão
travadas no CT-3 (`apps/api/tests/test_inventory_actions.py`).
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.jobs.models import FactoryPostingSchedule, ScheduledPost, VideoInventoryItem
from apps.jobs.services.media_cleanup import delete_file_field

# Margem quando não há horário planejado válido: nem publica no mesmo instante (o worker
# ainda precisa pegar a mensagem), nem empurra para longe.
RETRY_FALLBACK_DELAY = timedelta(seconds=30)


class InventoryActionError(Exception):
    """Erro de regra de negócio. A view traduz para 400 com esta mensagem no corpo."""


def remove_awaiting_item(inventory: VideoInventoryItem) -> dict:
    """
    Remove item aguardando postagem diretamente pelo inventário:
    - remove ScheduledPost vinculado (quando houver)
    - remove FactoryPostingSchedule vinculado (quando houver)
    - remove VideoInventoryItem
    - remove mídia local do corte

    Destrutivo e irreversível. As contagens devolvidas alimentam o aviso na tela, então
    contar a mais ou a menos mente para o usuário sem quebrar nada visível.
    """
    if inventory.status == "POSTED":
        raise InventoryActionError("Não é possível remover um vídeo já postado.")

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
        if corte:
            deleted_files += int(
                delete_file_field(
                    getattr(corte, "file", None),
                    operation="remove_awaiting",
                    inventory_item_id=inventory.id,
                    corte_id=corte.id,
                )
            )
            deleted_thumbnails += int(
                delete_file_field(
                    getattr(corte, "thumbnail", None),
                    operation="remove_awaiting",
                    inventory_item_id=inventory.id,
                    corte_id=corte.id,
                )
            )

        if scheduled_post_ids:
            ScheduledPost.objects.filter(id__in=scheduled_post_ids).delete()
        if schedules:
            FactoryPostingSchedule.objects.filter(id__in=[s.id for s in schedules]).delete()

        inventory_id = inventory.id
        inventory.delete()

    return {
        "ok": True,
        "deleted_inventory_item_id": inventory_id,
        "deleted_factory_schedule_count": len(schedules),
        "deleted_scheduled_post_count": len(scheduled_post_ids),
        "deleted_media_files": deleted_files,
        "deleted_media_thumbnails": deleted_thumbnails,
    }


def retry_posting_item(inventory: VideoInventoryItem, scheduled_at_raw=None) -> dict:
    """
    Reativa a postagem para um item aguardando do inventário:
    - ScheduledPost -> PENDING (mantendo horário planejado quando existir)
    - FactoryPostingSchedule -> PLANNED
    - VideoInventoryItem -> SCHEDULED
    - Enfileira tentativa imediata somente se for para agora

    `scheduled_at_raw` vem do payload e permite reagendar. Data ilegível não derruba a
    ação: cai no horário que já estava planejado.
    """
    if inventory.status == "POSTED":
        raise InventoryActionError("Este vídeo já foi postado.")

    schedule = (
        FactoryPostingSchedule.objects.select_related("scheduled_post")
        .filter(inventory_item=inventory)
        .order_by("-id")
        .first()
    )
    now = timezone.now()
    post = schedule.scheduled_post if schedule and schedule.scheduled_post_id else None
    next_try = _resolve_next_try(inventory, schedule, post, scheduled_at_raw, now)

    # Se não houver ScheduledPost, cria um agendamento imediato para permitir
    # "tentar novamente" direto do banco (status AVAILABLE/SCHEDULED sem post vinculado).
    if post is None:
        corte = getattr(inventory, "auto_cut_corte", None)
        if not corte:
            raise InventoryActionError("Item sem corte/mídia vinculada para postagem.")
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
        raise InventoryActionError("Este agendamento já foi concluído.")

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

    # Publicação avulsa: upa agora; o provedor (YouTube publishAt /
    # Upload-Post scheduled_date) faz o agendamento nativo no horário.
    post_to_platforms_task.delay(post.id)
    queued_immediately = True

    return {
        "ok": True,
        "inventory_item_id": inventory.id,
        "scheduled_post_id": post.id,
        "scheduled_for": next_try,
        "queued_immediately": queued_immediately,
    }


def _resolve_next_try(inventory, schedule, post, scheduled_at_raw, now):
    """Próximo horário de tentativa.

    Precedência: `scheduled_at` do payload > horário já planejado (schedule, inventário ou
    post, nessa ordem) > agora + 30s. O slot planejado é respeitado quando está no futuro —
    empurrar para "agora" furaria a janela que o planejamento diário montou.
    """
    if scheduled_at_raw:
        parsed = parse_datetime(str(scheduled_at_raw))
        if parsed:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed if parsed > now else (now + RETRY_FALLBACK_DELAY)

    planned_slot = (
        (schedule.scheduled_at if schedule else None)
        or inventory.scheduled_for
        or (post.scheduled_at if post else None)
    )
    return planned_slot if planned_slot and planned_slot > now else (now + RETRY_FALLBACK_DELAY)
