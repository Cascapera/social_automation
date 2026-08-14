"""Configuração do LLM em `settings` (refactor.md R-17 lote 3 / D-08).

Terceiro lote da migração `os.getenv` → `settings`, e o mais sensível: é a configuração do
provider, da chave e do modelo — errar aqui não quebra teste, quebra a conta.

Como nos lotes anteriores, o arquivo cobre as duas metades:

  1. **Equivalência** — cada setting devolve exatamente o que o `os.getenv` devolvia, com a
     mesma normalização e o mesmo default.
  2. **Anti-drift** — nenhum `os.getenv("LLM_*"/"XAI_*"/"GROK_*")` pode voltar a aparecer
     fora de `settings.py`.

A precedência entre as variáveis **não** está aqui: ela continua em `grok.py` e é testada
em `test_llm_provider.py`, que a migração deixou bem mais simples de escrever.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class LlmSettingsEquivalenceTests(SimpleTestCase):
    """`settings.X` devolve exatamente o que o `os.getenv` devolvia antes."""

    def test_provider_sai_normalizado_em_minusculas(self):
        """`_build_llm_client` compara com as chaves de `LLM_PROVIDER_DEFAULTS`, que são
        minúsculas. Sem o `.lower()`, `LLM_PROVIDER=Google` cairia no default da xAI e a
        instalação falaria com o provider errado."""
        self.assertEqual(settings.LLM_PROVIDER, (os.getenv("LLM_PROVIDER") or "xai").strip().lower())

    def test_chaves_e_modelos_saem_com_strip(self):
        """Todas eram lidas com `(os.getenv(...) or "").strip()`.

        O `strip` importa: uma chave copiada com espaço no fim continuaria "preenchida" e o
        erro apareceria como 401 do provider, não como configuração faltando.
        """
        for nome in (
            "LLM_API_KEY",
            "XAI_API_KEY",
            "LLM_MODEL",
            "LLM_MODEL_LIGHT",
            "GROK_MODEL",
            "LLM_BASE_URL",
            "GROK_PRICING_JSON",
        ):
            with self.subTest(variavel=nome):
                self.assertEqual(getattr(settings, nome), (os.getenv(nome) or "").strip())

    def test_limites_do_prompt_tem_piso_de_1(self):
        """`max(1, ...)`: zero ou negativo pediria "exatamente 0 cortes" ao modelo."""
        self.assertEqual(settings.LLM_MAX_SHORTS, max(1, int(os.getenv("LLM_MAX_SHORTS", "10"))))
        self.assertEqual(settings.LLM_MAX_LONGS, max(1, int(os.getenv("LLM_MAX_LONGS", "5"))))
        self.assertGreaterEqual(settings.LLM_MAX_SHORTS, 1)
        self.assertGreaterEqual(settings.LLM_MAX_LONGS, 1)

    def test_save_response_json_aceita_as_tres_formas_de_ligar(self):
        """Aqui o `.lower()` existe — diferente de `WHISPER_DEBUG_GPU`, que não tem.

        A inconsistência é do código de origem e foi preservada. Uniformizar mudaria o
        comportamento de quem já usa `TRUE` numa das duas.
        """
        esperado = (os.getenv("GROK_SAVE_RESPONSE_JSON") or "").strip().lower() in ("1", "true", "yes")
        self.assertEqual(settings.GROK_SAVE_RESPONSE_JSON, esperado)
        self.assertIsInstance(settings.GROK_SAVE_RESPONSE_JSON, bool)


class LlmConfigDriftTests(SimpleTestCase):
    """Anti-drift: a etapa (c) do R-17, travada em teste para este lote."""

    def test_nenhum_getenv_de_llm_fora_do_settings(self):
        padrao = re.compile(r'os\.(getenv|environ)\s*[(\[]\s*["\'](LLM_|XAI_|GROK_)')

        # O padrão precisa casar com a forma que existia antes do lote 3. Sem esta guarda,
        # uma regex quebrada faria o teste passar vazio para sempre.
        for forma_antiga in (
            '(os.getenv("LLM_API_KEY") or "").strip()',
            'max(1, int(os.getenv("LLM_MAX_SHORTS", "10")))',
            "os.environ['XAI_API_KEY']",
            '(os.getenv("GROK_PRICING_JSON") or "").strip()',
        ):
            self.assertRegex(forma_antiga, padrao)

        raiz = Path(__file__).resolve().parents[3]

        culpados = sorted(
            caminho.relative_to(raiz).as_posix()
            for caminho in raiz.rglob("*.py")
            if ".venv" not in caminho.parts
            and "tests" not in caminho.parts
            and caminho.name != "settings.py"
            and padrao.search(caminho.read_text(encoding="utf-8"))
        )

        self.assertEqual(
            culpados,
            [],
            "LLM_*/XAI_*/GROK_* lido fora de settings.py — use settings.X. "
            f"Ver R-17/D-08 no refactor.md. Arquivos: {culpados}",
        )
