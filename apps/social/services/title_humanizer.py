"""Leves variacoes no titulo para reduzir sinais de automacao.

Aplica apenas mudancas seguras (emoji). Nao altera ordem de palavras nem case.
"""
from __future__ import annotations

import random
import re

_EMOJI_POOL = ["🔥", "✨", "💡", "🎯", "📌", "👀"]

# Regex para detectar emoji no inicio (cobre a maioria dos pictogramas comuns)
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]",
    flags=re.UNICODE,
)

_REMOVE_PROBABILITY = 0.15
_PREPEND_PROBABILITY = 0.15


def humanize_title(title: str, *, rng: random.Random | None = None) -> str:
    """Retorna titulo com variacao leve de emoji.

    - 15% de chance de remover 1 emoji (se houver >=2)
    - 15% de chance de prefixar com emoji neutro (se nao comecar com emoji)
    - Caso contrario, retorna inalterado
    """
    if not title or not isinstance(title, str):
        return title or ""
    r = rng or random
    emojis = _EMOJI_PATTERN.findall(title)
    if len(emojis) >= 2 and r.random() < _REMOVE_PROBABILITY:
        target = r.choice(emojis)
        title = title.replace(target, "", 1)
        title = re.sub(r"\s+", " ", title).strip()
    elif r.random() < _PREPEND_PROBABILITY and not _EMOJI_PATTERN.match(title.strip()):
        emoji = r.choice(_EMOJI_POOL)
        title = f"{emoji} {title.strip()}"
    return title
