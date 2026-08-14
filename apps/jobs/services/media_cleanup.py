"""Remoção de mídia em disco, com a falha visível (refactor.md R-20 / D-10).

O D-10 contou 52 pontos que engolem exceção sem log, métrica ou sinal. Os mais caros são
os de remoção de mídia: quando um `corte.file.delete()` falha em silêncio, o registro sai
do banco e o arquivo fica no disco para sempre. Ninguém percebe — não há erro, não há
contador, e o espaço só aparece meses depois como "o disco encheu".

Este módulo não muda o **controle de fluxo**: a remoção continua tolerante a falha, porque
quem chama está no meio de uma operação que precisa terminar. O que muda é a
**visibilidade**: a falha vira um evento estruturado com o contexto de quem pediu.

> ⚠ Para quem monitora: depois deste item, `media_delete_failed` **passa a aparecer** nos
> logs. Volume subindo é diagnóstico ficando visível, não regressão nova.
"""

from __future__ import annotations

import logging
from pathlib import Path

from apps.jobs.logging_utils import log_event

logger = logging.getLogger(__name__)


def delete_file_field(campo, *, operation: str, **contexto) -> bool:
    """Apaga o arquivo de um `FileField`. Devolve `True` se apagou.

    `operation` identifica quem pediu a remoção (`remove_awaiting`, `mark_posted`, ...) —
    é o que permite ler o log e saber qual fluxo está deixando arquivo para trás.
    """
    if not campo:
        return False
    try:
        campo.delete(save=False)
        return True
    except Exception as e:
        log_event(
            logger,
            event="media_delete_failed",
            status="error",
            error=str(e),
            operation=operation,
            target="file_field",
            file_name=getattr(campo, "name", "") or "",
            **contexto,
        )
        return False


def unlink_path(caminho: Path | None, *, operation: str, **contexto) -> bool:
    """Apaga um caminho solto do disco. Devolve `True` se apagou.

    É o segundo passo de várias remoções: o Django já apagou pelo `FileField`, e isto
    limpa o arquivo que sobrou quando o path em disco diverge do que o campo aponta.
    """
    if not caminho:
        return False
    try:
        if not caminho.exists():
            return False
        caminho.unlink()
        return True
    except Exception as e:
        log_event(
            logger,
            event="media_delete_failed",
            status="error",
            error=str(e),
            operation=operation,
            target="path",
            file_name=str(caminho),
            **contexto,
        )
        return False
