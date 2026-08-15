"""Postar Imediato: publica hoje o que o agendamento publicaria no horário do slot.

O botão "Criar Agendamento" monta a agenda do dia e deixa a publicação para o beat, que
envia cada vídeo quando o slot chega. Este módulo é o outro botão: monta **a mesma** agenda
e publica **agora**.

Duas regras vêm de decisão do usuário e não de conveniência técnica (2026-08-15):

1. **Slot cujo horário já passou não entra.** O dia continua mandando em quantos e quais
   vídeos saem; o que já venceu fica de fora.
2. **O que entra é publicado agora**, não no horário do slot. Escolher amanhã às 14h de
   hoje publica os vídeos de amanhã hoje.

Nada da regra de publicação é reimplementado aqui: o envio é feito por
`process_brand_posting_queue_task`, a mesma task que o beat usa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.brands.models import Brand, Factory
from apps.jobs.logging_utils import log_event
from apps.jobs.services.factory_scheduler import (
    BrandDayPlan,
    factory_day_bounds,
    persist_planned_allocations,
    plan_brand_day,
)

logger = logging.getLogger(__name__)


@dataclass
class BrandImmediatePlan:
    brand: Brand
    plan: BrandDayPlan


def _brands_for(factory: Factory, brand_id: int | None) -> list[Brand]:
    qs = Brand.objects.filter(factory=factory).order_by("id")
    if brand_id:
        qs = qs.filter(id=brand_id)
    return list(qs)


def plan_immediate_post(
    factory: Factory,
    *,
    target_date: date,
    brand_id: int | None = None,
    for_update: bool,
    correlation_id: str = "",
) -> list[BrandImmediatePlan]:
    """Decide o que seria publicado, por brand. Não persiste alocação.

    `for_update=False` é o modo prévia: não trava linha de inventário e pode rodar fora de
    transação. `for_update=True` é o modo execução, e exige `transaction.atomic`.
    """
    now_utc = timezone.now()
    tz, day_start_utc, day_end_utc = factory_day_bounds(factory, target_date)
    now_local = now_utc.astimezone(tz)

    plans: list[BrandImmediatePlan] = []
    for brand in _brands_for(factory, brand_id):
        plan = plan_brand_day(
            factory=factory,
            brand=brand,
            local_day=target_date,
            tz=tz,
            now_local=now_local,
            day_start_utc=day_start_utc,
            day_end_utc=day_end_utc,
            # Decisão do usuário: slot vencido não entra no Postar Imediato.
            include_past_slots=False,
            correlation_id=correlation_id,
            for_update=for_update,
        )
        plans.append(BrandImmediatePlan(brand=brand, plan=plan))
    return plans


def _serialize_plan(factory: Factory, target_date: date, plans: list[BrandImmediatePlan]) -> dict:
    brands_payload = []
    total = 0
    for entry in plans:
        allocations = entry.plan.allocations
        total += len(allocations)
        brands_payload.append({
            "brand_id": entry.brand.id,
            "brand_name": entry.brand.name,
            "status": entry.plan.status,
            "videos": len(allocations),
            "slots_without_stock": entry.plan.slots_without_stock,
            "slots_already_scheduled": entry.plan.slots_already_scheduled,
            "slots": [
                {
                    "video_type": allocation.video_type,
                    "scheduled_at": allocation.scheduled_at.isoformat(),
                    "inventory_item_id": allocation.item.id,
                    "title": (allocation.item.title or "")[:200],
                }
                for allocation in allocations
            ],
        })
    return {
        "factory_id": factory.id,
        "target_date": str(target_date),
        "total": total,
        "brands": brands_payload,
    }


def preview_immediate_post(
    factory: Factory,
    *,
    target_date: date,
    brand_id: int | None = None,
) -> dict:
    """O que o Postar Imediato faria, sem fazer.

    ⚠ Não é 100% livre de escrita: `plan_brand_day` gera e grava o plano diário da brand se
    ele ainda não existir. É idempotente e é o mesmo plano que o agendamento automático
    usaria depois — está documentado no FEATURE_POSTAR_IMEDIATO.md.
    """
    plans = plan_immediate_post(
        factory,
        target_date=target_date,
        brand_id=brand_id,
        for_update=False,
    )
    return _serialize_plan(factory, target_date, plans)


def run_immediate_post(
    factory: Factory,
    *,
    target_date: date,
    brand_id: int | None = None,
    correlation_id: str = "",
) -> dict:
    """Cria os posts e dispara a publicação. Devolve o que foi enfileirado."""
    from apps.social.tasks import process_brand_posting_queue_task

    posts_by_brand: dict[int, list[int]] = {}
    result = {}

    with transaction.atomic():
        plans = plan_immediate_post(
            factory,
            target_date=target_date,
            brand_id=brand_id,
            for_update=True,
            correlation_id=correlation_id,
        )
        for entry in plans:
            if entry.plan.status != "ok" or not entry.plan.allocations:
                continue
            posts = persist_planned_allocations(
                factory=factory,
                allocations=entry.plan.allocations,
                publish_now=True,
                correlation_id=correlation_id,
            )
            if posts:
                posts_by_brand[entry.brand.id] = [post.id for post in posts]
        result = _serialize_plan(factory, target_date, plans)

        # Enfileirar só depois do commit: o worker é outro processo e, se a task partir com
        # a transação ainda aberta, ele procura posts que ainda não existem para ele.
        for bid, post_ids in sorted(posts_by_brand.items()):
            transaction.on_commit(
                lambda bid=bid, post_ids=post_ids: process_brand_posting_queue_task.delay(
                    bid, post_ids
                )
            )

    queued = sum(len(ids) for ids in posts_by_brand.values())
    log_event(
        logger,
        event="immediate_post_triggered",
        correlation_id=correlation_id,
        factory_id=factory.id,
        brand_id=brand_id,
        target_date=str(target_date),
        number_of_posts=queued,
        brand_count=len(posts_by_brand),
        status="success",
    )
    result["queued"] = queued
    return result
