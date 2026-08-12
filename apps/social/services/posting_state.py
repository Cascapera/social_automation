"""Dono único das transições de estado de publicação (refactor.md R-06 / D-02, D-03).

A transição "este vídeo foi publicado" coordena **4 modelos** — `ScheduledPost`,
`FactoryPostingSchedule`, `VideoInventoryItem` e `PostedVideoLog` — e até aqui estava
espalhada por cinco lugares sem dono, nenhum deles transacional. Uma falha no meio da
sequência deixava o conjunto inconsistente: `ScheduledPost` em `DONE` com o
`VideoInventoryItem` ainda em `SCHEDULED`, por exemplo. É exatamente a classe de
inconsistência que obrigou a existir o comando de reparo
`apps/social/management/commands/fix_youtube_posted_status.py`.

Este módulo é o começo da consolidação: `mark_posted` e `mark_still_scheduled` vieram de
`apps/social/tasks.py` (cópia C do mapa do R-03) **sem alteração de comportamento** — os
mesmos campos, os mesmos `update_fields`, o mesmo `PostedVideoLog`. A única diferença
observável é que agora cada transição é **atômica**.

⚠ REGRA DESTE MÓDULO: nada de I/O de rede dentro dos blocos `transaction.atomic()`. A
transação segura lock em até 4 tabelas; uma chamada HTTP lenta lá dentro transforma alguns
milissegundos de lock em segundos. Se precisar falar com o YouTube ou com o Upload-Post,
faça **antes** de entrar na transição e passe o resultado por parâmetro.

⚠ AINDA NÃO É O DONO ÚNICO. As outras quatro cópias da transição continuam de pé:

  A. `apps/social/tasks.py:414`  `_sync_factory_posting_schedule`, ramo YouTube-only
  B. `apps/social/tasks.py:452`  `_sync_factory_posting_schedule`, demais plataformas
  D. `apps/api/views.py:1447`    `VideoInventoryItemViewSet.mark_posted` (ação HTTP)
  E. `apps/social/management/commands/fix_youtube_posted_status.py:47`

Unificá-las é o **R-07**, que está bloqueado por 4 decisões pendentes (as divergências 2 a
5 registradas em L-7). Enquanto elas não forem tomadas, este módulo é o dono de *uma* das
cinco cópias — não das cinco. Os characterization tests do R-03 travam as diferenças.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.jobs.models import FactoryPostingSchedule, PostedVideoLog, ScheduledPost

# Espera antes de reconferir um item que o canal ainda não publicou.
STILL_SCHEDULED_RECHECK_MINUTES = 15
# Folga depois do horário de publicação declarado pelo canal, para evitar reconferir
# no exato instante em que o vídeo está indo ao ar.
PUBLISH_AT_GRACE_MINUTES = 5


def mark_posted(
    post: ScheduledPost,
    *,
    platform: str,
    external_video_id: str,
    metadata: dict | None = None,
) -> None:
    """Confirma que o vídeo está publicado na plataforma e fecha o ciclo dos 4 modelos.

    Leva `ScheduledPost` a `DONE` (sai da lista de espera), `FactoryPostingSchedule` a
    `DONE`, `VideoInventoryItem` a `POSTED` e registra o `PostedVideoLog` — deduplicando
    por `(item, plataforma, id externo)`.

    Não faz nada se o post não tiver um `FactoryPostingSchedule`: publicação avulsa não
    tem estado de factory para fechar.
    """
    schedule = (
        FactoryPostingSchedule.objects.filter(scheduled_post=post)
        .select_related("inventory_item", "factory", "brand")
        .first()
    )
    if not schedule:
        return
    item = schedule.inventory_item
    now = timezone.now()

    with transaction.atomic():
        post.status = "DONE"
        post.posted_at = post.posted_at or now
        post.error = ""
        # ScheduledPost não tem updated_at (ver apps/jobs/models.py:409-412) — incluí-lo
        # aqui levantava ValueError e abortava toda a reconciliação (R-21).
        post.save(update_fields=["status", "posted_at", "error"])

        schedule.status = "DONE"
        schedule.attempt_count = int(post.retry_count or 0)
        schedule.next_retry_at = None
        schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])

        item.status = "POSTED"
        item.posted_at = post.posted_at or now
        item.scheduled_for = post.scheduled_at
        item.last_error = ""
        item.attempt_count = int(post.retry_count or 0)
        item.save(
            update_fields=[
                "status",
                "posted_at",
                "scheduled_for",
                "last_error",
                "attempt_count",
                "updated_at",
            ]
        )

        if not PostedVideoLog.objects.filter(
            inventory_item=item,
            external_platform=platform,
            external_video_id=external_video_id,
        ).exists():
            PostedVideoLog.objects.create(
                factory=schedule.factory,
                brand=schedule.brand,
                inventory_item=item,
                external_platform=platform,
                external_video_id=external_video_id,
                posted_at=post.posted_at or now,
                metadata_snapshot={
                    "scheduled_post_id": post.id,
                    "platforms": post.platforms or [],
                    "external_ids": post.external_ids or {},
                    "youtube_verify": metadata or {},
                },
            )


def mark_still_scheduled(
    post: ScheduledPost,
    *,
    publish_at_raw: str | None,
    note: str = "",
) -> None:
    """Mantém o item como agendado no canal, sem confirmar publicação.

    É o caso do vídeo que já subiu para o YouTube como agendado mas ainda não foi ao ar.
    Marca o próximo horário de reconferência e devolve o item para `SCHEDULED`.

    Não faz nada se o post não tiver um `FactoryPostingSchedule`.
    """
    schedule = (
        FactoryPostingSchedule.objects.filter(scheduled_post=post)
        .select_related("inventory_item")
        .first()
    )
    if not schedule:
        return
    item = schedule.inventory_item

    next_check = timezone.now() + timedelta(minutes=STILL_SCHEDULED_RECHECK_MINUTES)
    publish_at = parse_datetime(str(publish_at_raw or "")) if publish_at_raw else None
    if publish_at:
        if timezone.is_naive(publish_at):
            publish_at = timezone.make_aware(publish_at, timezone.get_current_timezone())
        # Reconferir logo depois do horário real de publicação no canal.
        next_check = max(next_check, publish_at + timedelta(minutes=PUBLISH_AT_GRACE_MINUTES))

    with transaction.atomic():
        schedule.status = "PLANNED"
        schedule.next_retry_at = next_check
        schedule.save(update_fields=["status", "next_retry_at", "updated_at"])

        item.status = "SCHEDULED"
        item.last_error = note or "Agendado no YouTube. Aguardando publicação no canal."
        item.save(update_fields=["status", "last_error", "updated_at"])
