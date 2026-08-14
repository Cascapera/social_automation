"""Preflight da publicação (refactor.md R-09 / D-01).

Primeira das quatro fatias de `_run_post_to_platforms`. Responde a uma pergunta só:
**este post pode ser publicado agora?** — e, se puder, entrega tudo que o resto do fluxo
precisa já resolvido.

São seis guardas, nesta ordem, e a ordem importa:

  1. o post existe;
  2. está em `PENDING` (entrega duplicada do Celery é normal, não excepcional);
  3. tem origem utilizável — job com render pronto **ou** corte finalizado;
  4. o slot da factory ainda não venceu;
  5. não há uma publicação anterior no Upload-Post esperando reconciliação;
  6. o claim atômico `PENDING → POSTING` foi ganho por esta execução.

⚠ **Cada saída antecipada devolve exatamente o mesmo `dict` de antes.** Esses dicts são
lidos pelo chamador e aparecem em log e em teste — mudar uma chave ou uma mensagem aqui é
mudança de comportamento, não refatoração. O CT-2 (R-04) cobre as 9 saídas antecipadas.

O claim da guarda 6 é o que impede dois workers de publicarem o mesmo vídeo: quem perde a
corrida recebe `{"skipped": ...}` e sai. Ele fecha o preflight de propósito — depois dele o
post já está `POSTING` e a responsabilidade é de quem chamou.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.jobs.logging_utils import Timer, resolve_scheduled_post_correlation_id
from apps.jobs.models import RenderOutput, ScheduledPost
from apps.social.services.publish_targets import _resolve_post_target_brand
from apps.social.services.publishing.reconciliation import (
    _try_pending_upload_post_reconciliation,
)
from apps.social.services.publishing.slots import _fail_expired_factory_slot


@dataclass(frozen=True)
class PreflightOk:
    """O que o resto do fluxo precisa, já resolvido."""

    post: ScheduledPost
    brand: Any
    job: Any
    video_path: str
    correlation_id: str
    current_attempt: int
    timer: Timer


@dataclass(frozen=True)
class EarlyExit:
    """Uma das saídas antecipadas. `payload` é o dict devolvido pela task, sem alteração."""

    payload: dict


def preflight(scheduled_post_id: int) -> PreflightOk | EarlyExit:
    """Carrega, valida e reivindica o post. Devolve o contexto pronto ou a saída antecipada."""
    timer = Timer()

    try:
        post = ScheduledPost.objects.select_related(
            "job",
            "job__brand",
            "social_account",
            "auto_cut_corte",
            "auto_cut_corte__analysis",
            "auto_cut_corte__suggestion",
            "factory_schedule",
        ).get(id=scheduled_post_id)
    except ScheduledPost.DoesNotExist:
        return EarlyExit({"error": "ScheduledPost não encontrado"})

    correlation_id = resolve_scheduled_post_correlation_id(post)

    if post.status != "PENDING":
        return EarlyExit({"skipped": "status não é PENDING"})

    current_attempt = int(post.retry_count or 0) + 1
    brand = None
    video_path = ""
    job_obj = post.job

    if post.job_id:
        brand = post.job.brand
        if not brand:
            post.status = "FAILED"
            post.error = "Job sem marca"
            post.save(update_fields=["status", "error"])
            return EarlyExit({"error": "Job sem marca"})
        # Reverse OneToOne: the attribute access itself raises when the job has no
        # RenderOutput at all, so the guard below would never run (R-22).
        try:
            output = post.job.output
        except RenderOutput.DoesNotExist:
            output = None
        if not output or not output.file:
            post.status = "FAILED"
            post.error = "Job sem vídeo final"
            post.save(update_fields=["status", "error"])
            return EarlyExit({"error": "Job sem vídeo final"})
        video_path = output.file.path
    elif post.auto_cut_corte_id:
        corte = post.auto_cut_corte
        brand = corte.analysis.brand if corte and corte.analysis_id else None
        if not brand:
            post.status = "FAILED"
            post.error = "AutoCut sem marca"
            post.save(update_fields=["status", "error"])
            return EarlyExit({"error": "AutoCut sem marca"})
        if not corte.file:
            post.status = "FAILED"
            post.error = "AutoCut sem vídeo finalizado"
            post.save(update_fields=["status", "error"])
            return EarlyExit({"error": "AutoCut sem vídeo finalizado"})
        video_path = corte.file.path
    else:
        post.status = "FAILED"
        post.error = "ScheduledPost sem origem (job/corte)"
        post.save(update_fields=["status", "error"])
        return EarlyExit({"error": "ScheduledPost sem origem"})

    # In factory context, prefer schedule destination brand for account/credential.
    target_brand = _resolve_post_target_brand(post)
    if target_brand:
        brand = target_brand

    _brand_id = brand.id if brand else None

    expired_result = _fail_expired_factory_slot(
        post,
        correlation_id=correlation_id,
        brand_id=_brand_id,
        current_attempt=current_attempt,
        duration_ms=timer.elapsed_ms(),
        reason="O horário do slot já passou antes de iniciar uma nova tentativa de publicação.",
    )
    if expired_result is not None:
        return EarlyExit(expired_result)

    early_reconcile = _try_pending_upload_post_reconciliation(
        post,
        brand,
        correlation_id=correlation_id,
        brand_id=_brand_id,
        current_attempt=current_attempt,
        _timer=timer,
    )
    if early_reconcile is not None:
        return EarlyExit(early_reconcile)

    claimed = ScheduledPost.objects.filter(id=post.id, status="PENDING").update(status="POSTING")
    if not claimed:
        return EarlyExit({"skipped": "status não é PENDING"})
    post.status = "POSTING"

    return PreflightOk(
        post=post,
        brand=brand,
        job=job_obj,
        video_path=video_path,
        correlation_id=correlation_id,
        current_attempt=current_attempt,
        timer=timer,
    )
