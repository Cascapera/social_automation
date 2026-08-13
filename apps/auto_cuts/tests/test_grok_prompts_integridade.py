"""Integridade dos prompts do Grok (refactor.md R-18 / D-09).

O R-18 tirou 1.184 linhas de `services/grok.py` — prompts, vocabulário e tabela de preço —
e as pôs em `apps/auto_cuts/prompts/` e `grok_pricing.py`. A exigência do plano para essa
mudança é explícita: *"teste que faz hash dos prompts montados antes e depois e compara. Se
um único caractere mudou, o PR está errado."* Os valores abaixo foram capturados **antes**
da mudança, rodando contra o `grok.py` de 2.057 linhas.

O teste continua valendo depois do R-18, e é aí que ele fica mais útil. O D-09 reclamava
que "ajustar uma palavra de um prompt exige mexer no mesmo arquivo que contém o cliente
HTTP, e o diff de 'mudei o prompt' tem o mesmo peso de revisão que 'mudei o cliente'".
Separar os arquivos resolveu metade; a outra metade é esta: uma mudança de prompt **quebra
o build** e obriga quem a fez a atualizar o hash no mesmo PR.

**Se você mudou um prompt de propósito**, o teste vai falhar dizendo qual. Rode-o, copie o
hash novo para cá e descreva a mudança editorial na mensagem do commit. A falha não é um
obstáculo — é o registro de que o texto enviado ao modelo mudou.
"""

from __future__ import annotations

import hashlib

from django.test import SimpleTestCase

from apps.auto_cuts import prompts
from apps.auto_cuts.services import grok
from apps.auto_cuts.services.grok_pricing import GROK_PRICING

HASHES_CONSTANTES = {
    'ALL_THEME_CATEGORIES': 'c760dcae371fcf83',
    'ANTI_AUTOMATION_RULES_EN': '94b5b54f0423f64e',
    'ANTI_AUTOMATION_RULES_PT': '117ec0a1ab3f95d9',
    'CHUNKS_PROMPT_TEMPLATE': '2cdd1a4cb3b6ffb0',
    'CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL': '7cb5f75aa4b0cac5',
    'CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL_EN': 'e320e50ac5b37bbe',
    'CHUNKS_PROMPT_TEMPLATE_VIRAL_EN': 'b0159b75963726fb',
    'CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG': '3b7fd37322e7c2cb',
    'CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG_EN': '67f5c62b70f3e978',
    'CHUNKS_PROMPT_TEMPLATE_VIRAL_TRANSLATE': '8c083fc63f34a699',
    'CTR_WORDS_EN': '7571563aa121e0c4',
    'CTR_WORDS_PT': 'fef4ba08cc9239ca',
    'FORBIDDEN_WORDS_EN': 'fb678fc13d72509e',
    'FORBIDDEN_WORDS_PT': '7d8ff4707c6e35da',
    'GROK_MODEL_ALIASES': '4153e18be66f3db9',
    'GROK_OPERATION_ANALYZE_CHUNKS': 'd552206ce49eca7e',
    'GROK_OPERATION_READY_CUTS_TITLES_FROM_JOB_NAME': '5e634ae8058928b3',
    'GROK_OPERATION_READY_CUTS_TITLES_FROM_TRANSCRIPTS': 'f1238cd9a4ff156b',
    'GROK_OPERATION_READY_CUT_METADATA': 'fbfb1161d1b87716',
    'GROK_PRICING': '06846f774a00217b',
    'LLM_PROVIDER_DEFAULTS': 'eedc346bc40211f8',
    'METADATA_SAFETY_RULES_EN': 'bf85eff6a10fc0b2',
    'METADATA_SAFETY_RULES_PT': 'd50ece86453efe7b',
    'READY_CUT_SYSTEM_PROMPT_BASE': '8d2fccb940273b6f',
    'SYSTEM_PROMPT': 'dedb3033cfbe93d3',
    'SYSTEM_PROMPT_EDUCATIONAL': '3d5e5c835ff636fa',
    'SYSTEM_PROMPT_EDUCATIONAL_EN': '5ae895188b106216',
    'SYSTEM_PROMPT_VIRAL_EN': 'ca78a0d503d7a023',
    'SYSTEM_PROMPT_VIRAL_LONG': '19671bad491df50d',
    'SYSTEM_PROMPT_VIRAL_LONG_EN': 'ca9a9204531168d7',
    'SYSTEM_PROMPT_VIRAL_TRANSLATE': 'e3422352e5444341',
}

HASHES_MONTADOS = {
    "_ready_cuts_batch_jobname_system_prompt('')": '981ed87ccebcdce3',
    "_ready_cuts_batch_jobname_system_prompt('en')": '7d3115013d64d73b',
    "_ready_cuts_batch_jobname_system_prompt('es')": '981ed87ccebcdce3',
    "_ready_cuts_batch_jobname_system_prompt('pt')": '981ed87ccebcdce3',
    "_ready_cuts_batch_transcripts_system_prompt('')": '832ee7379b99409f',
    "_ready_cuts_batch_transcripts_system_prompt('en')": '5862ca7d476be29f',
    "_ready_cuts_batch_transcripts_system_prompt('es')": '832ee7379b99409f',
    "_ready_cuts_batch_transcripts_system_prompt('pt')": '832ee7379b99409f',
    "_ready_cuts_metadata_language_block('')": '70b2f868eabbaac6',
    "_ready_cuts_metadata_language_block('en')": '81233303edf314dc',
    "_ready_cuts_metadata_language_block('es')": '70b2f868eabbaac6',
    "_ready_cuts_metadata_language_block('pt')": '70b2f868eabbaac6',
}

def _hash(valor) -> str:
    return hashlib.sha256(repr(valor).encode("utf-8")).hexdigest()[:16]


def _constante(nome):
    """Busca o nome onde ele mora hoje — no pacote de prompts ou ainda no grok."""
    for modulo in (prompts, grok):
        if hasattr(modulo, nome):
            return getattr(modulo, nome)
    if nome == "GROK_PRICING":
        return GROK_PRICING
    raise AssertionError(
        f"{nome} sumiu do código. Se foi removido de propósito, tire a entrada de "
        "HASHES_CONSTANTES no mesmo PR e diga por quê."
    )


class PromptsNaoMudaramTests(SimpleTestCase):
    """Nenhum caractere de nenhum prompt mudou desde a captura do baseline."""

    def test_constantes_batem_com_o_hash_congelado(self):
        for nome, esperado in sorted(HASHES_CONSTANTES.items()):
            with self.subTest(constante=nome):
                self.assertEqual(
                    _hash(_constante(nome)),
                    esperado,
                    f"O conteúdo de {nome} mudou. Se foi de propósito, atualize o hash "
                    "aqui no mesmo PR — é o que faz a mudança de prompt aparecer na revisão.",
                )

    def test_prompts_montados_por_funcao_batem_com_o_hash_congelado(self):
        """Os prompts de cortes prontos são concatenados em runtime, não declarados.

        `READY_CUT_SYSTEM_PROMPT_BASE` + bloco de idioma vira o texto final. Congelar só a
        base deixaria a montagem livre para mudar sem ninguém ver.
        """
        construtores = {
            "_ready_cuts_metadata_language_block": grok._ready_cuts_metadata_language_block,
            "_ready_cuts_batch_transcripts_system_prompt": grok._ready_cuts_batch_transcripts_system_prompt,
            "_ready_cuts_batch_jobname_system_prompt": grok._ready_cuts_batch_jobname_system_prompt,
        }
        for chave, esperado in sorted(HASHES_MONTADOS.items()):
            nome, _, resto = chave.partition("(")
            idioma = resto.rstrip(")").strip("'")
            with self.subTest(prompt=chave):
                self.assertEqual(_hash(construtores[nome](idioma)), esperado)


class CoberturaDoCongelamentoTests(SimpleTestCase):
    """Prompt novo sem hash não pode passar despercebido.

    Sem esta verificação, a proteção só valeria para os prompts que existiam no dia do
    R-18: bastaria adicionar `SYSTEM_PROMPT_NOVO` para ter conteúdo indo ao modelo sem
    nenhum registro de quando mudou.
    """

    def test_todo_prompt_publico_tem_hash_congelado(self):
        expostos = {
            nome
            for nome in getattr(prompts, "__all__", [])
            if isinstance(getattr(prompts, nome), (str, list, set, dict, tuple))
        }
        sem_hash = sorted(expostos - set(HASHES_CONSTANTES))

        self.assertEqual(
            sem_hash,
            [],
            "Prompt exportado sem hash congelado. Rode o teste, pegue o hash e adicione "
            f"em HASHES_CONSTANTES. Faltando: {sem_hash}",
        )
