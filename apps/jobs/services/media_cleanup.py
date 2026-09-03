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
import shutil
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


def resolve_media_path(campo) -> Path | None:
    """Caminho em disco de um `FileField`, ou `None` quando não dá para saber.

    Não loga de propósito: nem todo storage tem caminho local, e campo sem arquivo é o
    caso normal. Serve só para procurar sobra em disco depois de apagar pelo Django.
    """
    if not campo or not getattr(campo, "name", ""):
        return None
    try:
        return Path(campo.path)
    except Exception:
        return None


def delete_media_pair(campo, *, operation: str, **contexto) -> bool:
    """Apaga o arquivo pelo `FileField` **e** a sobra em disco. `True` se algo saiu.

    Os dois passos existem porque eles falham por motivos diferentes: o do Django pode
    falhar por storage, e o do disco resolve o caso em que o caminho gravado no banco
    diverge do arquivo que está lá. Antes do R-20, os dois erravam calados.
    """
    caminho = resolve_media_path(campo)
    apagou_campo = delete_file_field(campo, operation=operation, **contexto)
    apagou_sobra = unlink_path(caminho, operation=operation, **contexto)
    return apagou_campo or apagou_sobra


def rmtree_path(caminho: Path | None, *, operation: str, **contexto) -> bool:
    """Apaga um diretório inteiro. `True` se apagou."""
    if not caminho:
        return False
    try:
        if not caminho.exists():
            return False
        shutil.rmtree(caminho)
        return True
    except Exception as e:
        log_event(
            logger,
            event="media_delete_failed",
            status="error",
            error=str(e),
            operation=operation,
            target="directory",
            file_name=str(caminho),
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
