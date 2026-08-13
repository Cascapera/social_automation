"""Dono único das transições de estado de publicação (refactor.md R-06/R-07, D-02, D-03).

A transição "este vídeo foi publicado" coordena **4 modelos** — `ScheduledPost`,
`FactoryPostingSchedule`, `VideoInventoryItem` e `PostedVideoLog` — e até o R-06 estava
espalhada por cinco lugares sem dono, nenhum deles transacional. Uma falha no meio da
sequência deixava o conjunto inconsistente: `ScheduledPost` em `DONE` com o
`VideoInventoryItem` ainda em `SCHEDULED`, por exemplo. É exatamente a classe de
inconsistência que obrigou a existir o comando de reparo
`apps/social/management/commands/fix_youtube_posted_status.py`.

**Desde o R-07 este módulo é o dono único.** Os cinco sites que escreviam a transição à
mão passaram a chamar `mark_posted` ou `mark_item_posted`:

  A. `apps/social/tasks.py`  `_sync_factory_posting_schedule`, ramo YouTube-only
  B. `apps/social/tasks.py`  `_sync_factory_posting_schedule`, demais plataformas
  C. `apps/social/tasks.py`  reconciliação (era `_mark_factory_posting_verified`)
  D. `apps/api/views.py`     `VideoInventoryItemViewSet.mark_posted` (ação HTTP)
  E. `apps/social/management/commands/fix_youtube_posted_status.py`

`grep 'status = "POSTED"' apps/` deve retornar **apenas este arquivo**. Se voltar a
retornar dois, uma sexta cópia nasceu — é o começo do D-02 de novo.

Regras canônicas, decididas em 2026-08-13 (L-7 do refactor.md). Onde as cópias
divergiam, venceu:

  1. `PostedVideoLog` é **sempre** deduplicado por `(item, plataforma, id externo)` e
     **nunca** é gravado com `external_video_id` vazio. Antes só o ramo não-YouTube
     violava as duas coisas — era bug, não intenção.
  2. `schedule.next_retry_at` é **sempre** zerado. Um schedule em `DONE` com retry
     pendente é estado sujo, e o varredor de retry pode voltar a olhar para ele.
  3. `attempt_count` (schedule e item) é **sempre** sincronizado com `post.retry_count`.
     O número real de tentativas está no post, independente de quem fechou o ciclo.
  4. `item.posted_at` e `item.scheduled_for` são **preservados** quando já têm valor.
     No fluxo normal estão vazios quando a transição roda, então nada muda ali; a regra
     existe para o caso de reparo, onde reescrever por cima perderia o horário real.
  5. O `ScheduledPost` é **sempre** levado a `DONE` dentro da transição. Para os sites
     que já rodam sobre um post `DONE` isso é no-op — e o `save()` só acontece quando
     algum campo muda de fato, para não gerar escrita à toa.

⚠ REGRA DESTE MÓDULO: nada de I/O de rede dentro dos blocos `transaction.atomic()`. A
transação segura lock em até 4 tabelas; uma chamada HTTP lenta lá dentro transforma alguns
milissegundos de lock em segundos. Se precisar falar com o YouTube ou com o Upload-Post,
faça **antes** de entrar na transição e passe o resultado por parâmetro.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.jobs.models import (
    FactoryPostingSchedule,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)

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
    log_metadata: dict | None = None,
) -> bool:
    """Fecha o ciclo de publicação a partir do `ScheduledPost`.

    Leva `ScheduledPost` a `DONE`, `FactoryPostingSchedule` a `DONE`,
    `VideoInventoryItem` a `POSTED` e registra o `PostedVideoLog`. Tudo numa transação.

    Devolve `False` sem tocar em nada quando o post não tem `FactoryPostingSchedule`:
    publicação avulsa não tem estado de factory para fechar. É comportamento antigo,
    preservado de propósito — quem depende dele são as tasks de reconciliação.
    """
    schedule = (
        FactoryPostingSchedule.objects.filter(scheduled_post=post)
        .select_related("inventory_item", "factory", "brand")
        .first()
    )
    if not schedule:
        return False

    with transaction.atomic():
        effective_posted_at = _close_post(post, posted_at=None)
        attempts = int(post.retry_count or 0)
        _close_schedule(schedule, attempt_count=attempts)
        _close_item(
            schedule.inventory_item,
            posted_at=effective_posted_at,
            scheduled_for=post.scheduled_at,
            attempt_count=attempts,
        )
        _record_posted_log(
            item=schedule.inventory_item,
            factory=schedule.factory,
            brand=schedule.brand,
            platform=platform,
            external_video_id=external_video_id,
            posted_at=effective_posted_at,
            post=post,
            log_metadata=log_metadata,
        )
    return True


def mark_item_posted(
    item: VideoInventoryItem,
    *,
    platform: str,
    external_video_id: str,
    posted_at: datetime | None = None,
    log_metadata: dict | None = None,
) -> None:
    """Fecha o ciclo a partir do `VideoInventoryItem`, e não de um post específico.

    É a marcação manual pela API: o operador aponta para o item, não para a tentativa de
    publicação. Fecha **todos** os schedules do item e os posts de cada um — e funciona
    para item sem schedule nenhum, caso em que só o item e o log são escritos.

    `posted_at` explícito é a **única** exceção à regra 4: uma data digitada pelo operador
    é afirmação deliberada e sobrescreve o que houver. Sem ela, o horário existente é
    preservado como em todos os outros caminhos.

    `attempt_count` do item recebe o maior `retry_count` entre os posts envolvidos: com
    vários schedules, é a leitura que não esconde tentativa nenhuma.
    """
    schedules = list(
        FactoryPostingSchedule.objects.filter(inventory_item=item)
        .select_related("scheduled_post")
        .order_by("id")
    )

    with transaction.atomic():
        effective_posted_at = posted_at or item.posted_at or timezone.now()
        item_attempts = int(item.attempt_count or 0)
        scheduled_for = item.scheduled_for
        for schedule in schedules:
            post = schedule.scheduled_post
            attempts = 0
            if post:
                _close_post(post, posted_at=posted_at)
                attempts = int(post.retry_count or 0)
                scheduled_for = scheduled_for or post.scheduled_at
            _close_schedule(schedule, attempt_count=attempts)
            item_attempts = max(item_attempts, attempts)

        _close_item(
            item,
            posted_at=effective_posted_at,
            scheduled_for=scheduled_for,
            attempt_count=item_attempts,
            overwrite_posted_at=posted_at is not None,
        )
        _record_posted_log(
            item=item,
            factory=item.factory,
            brand=item.brand,
            platform=platform,
            external_video_id=external_video_id,
            posted_at=effective_posted_at,
            post=next((s.scheduled_post for s in schedules if s.scheduled_post), None),
            log_metadata=log_metadata,
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


# --------------------------------------------------------------------------------------
# Passos internos. São os únicos lugares do projeto que escrevem "publicado" nos 4 modelos.
# --------------------------------------------------------------------------------------


def _close_post(post: ScheduledPost, *, posted_at: datetime | None) -> datetime:
    """Regra 5 — leva o post a `DONE` e devolve o horário efetivo de publicação.

    Só grava quando algo muda de fato: os sites que já rodam sobre um post `DONE` não
    pagam um `UPDATE` a mais por chamarem o dono da transição.
    """
    effective = posted_at or post.posted_at or timezone.now()
    if post.status == "DONE" and post.posted_at == effective and not post.error:
        return effective
    post.status = "DONE"
    post.posted_at = effective
    post.error = ""
    # ScheduledPost não tem updated_at (ver apps/jobs/models.py) — incluí-lo aqui
    # levantava ValueError e abortava toda a reconciliação (R-21).
    post.save(update_fields=["status", "posted_at", "error"])
    return effective


def _close_schedule(schedule: FactoryPostingSchedule, *, attempt_count: int) -> None:
    """Regras 2 e 3 — `DONE`, sem retry pendente, com o número real de tentativas."""
    schedule.status = "DONE"
    schedule.attempt_count = attempt_count
    schedule.next_retry_at = None
    schedule.save(update_fields=["status", "attempt_count", "next_retry_at", "updated_at"])


def _close_item(
    item: VideoInventoryItem,
    *,
    posted_at: datetime,
    scheduled_for: datetime | None,
    attempt_count: int,
    overwrite_posted_at: bool = False,
) -> None:
    """Regras 3 e 4 — `POSTED`, sem erro, preservando horários que já existam.

    `overwrite_posted_at` só é ligado pela marcação manual com data explícita: é o
    operador afirmando o horário, e aí a afirmação dele vale mais que o valor guardado.
    """
    item.status = "POSTED"
    item.posted_at = posted_at if overwrite_posted_at else (item.posted_at or posted_at)
    item.scheduled_for = item.scheduled_for or scheduled_for
    item.last_error = ""
    item.attempt_count = attempt_count
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


def _record_posted_log(
    *,
    item: VideoInventoryItem,
    factory,
    brand,
    platform: str,
    external_video_id: str,
    posted_at: datetime,
    post: ScheduledPost | None,
    log_metadata: dict | None,
) -> None:
    """Regra 1 — um log por `(item, plataforma, id externo)`, e nunca com id vazio.

    Sem id externo não há o que auditar: a linha só diria "algo foi publicado em algum
    lugar", e ainda quebraria a deduplicação de todas as chamadas seguintes.
    """
    if not external_video_id or not brand:
        return
    if PostedVideoLog.objects.filter(
        inventory_item=item,
        external_platform=platform,
        external_video_id=external_video_id,
    ).exists():
        return

    snapshot = {
        "scheduled_post_id": post.id if post else None,
        "platforms": (post.platforms or []) if post else [],
        "external_ids": (post.external_ids or {}) if post else {},
    }
    snapshot.update(log_metadata or {})
    PostedVideoLog.objects.create(
        factory=factory,
        brand=brand,
        inventory_item=item,
        external_platform=platform,
        external_video_id=external_video_id,
        posted_at=posted_at,
        metadata_snapshot=snapshot,
    )
