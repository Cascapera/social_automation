"""Prompts do pipeline de auto-cuts, separados do cliente HTTP (refactor.md R-18 / D-09).

Antes, `services/grok.py` tinha 2.057 linhas e a primeira função só aparecia na linha
1.236: até ali era tudo prompt, vocabulário e tabela de preço. Prompt é **conteúdo** — muda
por motivo editorial, é revisado por outra pessoa e com outros olhos que um cliente HTTP.
Dividir o arquivo dá a cada um o ciclo de vida que ele já tinha na prática, e devolve o
`git blame` de quem escreveu o quê.

  vocabulary.py  blocos de regra e listas reaproveitados pelos prompts
  system.py      o papel que o modelo assume, um por variação de análise
  templates.py   a mensagem de usuário, pareada com o system prompt correspondente

Este `__init__` reexporta tudo: quem consome prompts não precisa saber em qual dos três
arquivos cada nome mora.
"""

from apps.auto_cuts.prompts.system import (
    READY_CUT_SYSTEM_PROMPT_BASE,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_EDUCATIONAL,
    SYSTEM_PROMPT_EDUCATIONAL_EN,
    SYSTEM_PROMPT_VIRAL_EN,
    SYSTEM_PROMPT_VIRAL_LONG,
    SYSTEM_PROMPT_VIRAL_LONG_EN,
    SYSTEM_PROMPT_VIRAL_TRANSLATE,
)
from apps.auto_cuts.prompts.templates import (
    CHUNKS_PROMPT_TEMPLATE,
    CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL,
    CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL_EN,
    CHUNKS_PROMPT_TEMPLATE_VIRAL_EN,
    CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG,
    CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG_EN,
    CHUNKS_PROMPT_TEMPLATE_VIRAL_TRANSLATE,
)
from apps.auto_cuts.prompts.vocabulary import (
    ALL_THEME_CATEGORIES,
    ANTI_AUTOMATION_RULES_EN,
    ANTI_AUTOMATION_RULES_PT,
    CTR_WORDS_EN,
    CTR_WORDS_PT,
    FORBIDDEN_WORDS_EN,
    FORBIDDEN_WORDS_PT,
    METADATA_SAFETY_RULES_EN,
    METADATA_SAFETY_RULES_PT,
)

__all__ = [
    "ALL_THEME_CATEGORIES",
    "ANTI_AUTOMATION_RULES_EN",
    "ANTI_AUTOMATION_RULES_PT",
    "CHUNKS_PROMPT_TEMPLATE",
    "CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL",
    "CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL_EN",
    "CHUNKS_PROMPT_TEMPLATE_VIRAL_EN",
    "CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG",
    "CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG_EN",
    "CHUNKS_PROMPT_TEMPLATE_VIRAL_TRANSLATE",
    "CTR_WORDS_EN",
    "CTR_WORDS_PT",
    "FORBIDDEN_WORDS_EN",
    "FORBIDDEN_WORDS_PT",
    "METADATA_SAFETY_RULES_EN",
    "METADATA_SAFETY_RULES_PT",
    "READY_CUT_SYSTEM_PROMPT_BASE",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_EDUCATIONAL",
    "SYSTEM_PROMPT_EDUCATIONAL_EN",
    "SYSTEM_PROMPT_VIRAL_EN",
    "SYSTEM_PROMPT_VIRAL_LONG",
    "SYSTEM_PROMPT_VIRAL_LONG_EN",
    "SYSTEM_PROMPT_VIRAL_TRANSLATE",
]
