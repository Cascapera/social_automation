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

## O que entra no congelamento, e o que não entra

**Só entra conteúdo que vai para o modelo:** prompts, templates e os blocos de vocabulário
que eles concatenam. É o que está exportado em `apps.auto_cuts.prompts.__all__`, e as duas
listas são verificadas uma contra a outra em `CoberturaDoCongelamentoTests`.

**Não entra dado de negócio nem configuração de cliente** — `GROK_PRICING`,
`GROK_MODEL_ALIASES`, `LLM_PROVIDER_DEFAULTS`, `GROK_OPERATION_*`. Esses mudam por motivo
legítimo e rotineiro: o fornecedor reajusta o preço, um modelo é descontinuado, um provedor
novo entra. Travá-los cobraria cerimônia de revisão sem nada em troca.

> **Esta regra nasceu de um erro meu, no dia seguinte ao R-18.** A primeira versão congelou
> *toda* constante em maiúsculas, porque o baseline foi capturado com uma varredura ampla —
> o que era certo para **provar que a mudança de casa foi pura**, e errado como **trava
> permanente**. As duas coisas têm escopos diferentes e eu usei a mesma lista para as duas.
> O commit `343acbc`, que só adicionava o preço do `gemini-2.5-flash`, quebrou o build por
> isso. O teste funcionou — só estava mirando no alvo errado.
"""

from __future__ import annotations

import hashlib

from django.test import SimpleTestCase

from apps.auto_cuts import prompts
from apps.auto_cuts.services import grok

HASHES_CONSTANTES = {
    'ALL_THEME_CATEGORIES': 'c760dcae371fcf83',
    'ANTI_AUTOMATION_RULES_EN': '94b5b54f0423f64e',
    'ANTI_AUTOMATION_RULES_PT': '117ec0a1ab3f95d9',
    'AUTHOR_CUE_RULES_EN': '9b0310140f31a138',
    'AUTHOR_CUE_RULES_PT': 'eba252cbfb1745f2',
    'CHUNKS_PROMPT_TEMPLATE': '068dfb1379a59232',
    'CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL': '42a5712ebf3fb2af',
    'CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL_EN': 'f6e0737883efceea',
    'CHUNKS_PROMPT_TEMPLATE_VIRAL_EN': 'f8c9d4f5e699c9e0',
    'CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG': '58d4d414d298b8ac',
    'CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG_EN': '1915f2d875af904e',
    'CHUNKS_PROMPT_TEMPLATE_VIRAL_TRANSLATE': '296788e8df40611e',
    'CTR_WORDS_EN': '7571563aa121e0c4',
    'CTR_WORDS_PT': 'fef4ba08cc9239ca',
    'FORBIDDEN_WORDS_EN': 'fb678fc13d72509e',
    'FORBIDDEN_WORDS_PT': '7d8ff4707c6e35da',
    'METADATA_SAFETY_RULES_EN': 'bf85eff6a10fc0b2',
    'METADATA_SAFETY_RULES_PT': 'd50ece86453efe7b',
    'READY_CUT_SYSTEM_PROMPT_BASE': '8d2fccb940273b6f',
    'SYSTEM_PROMPT': '4774cc68ef675ebb',
    'SYSTEM_PROMPT_EDUCATIONAL': '13cc7be5344522e4',
    'SYSTEM_PROMPT_EDUCATIONAL_EN': 'f6ec002ad597c729',
    'SYSTEM_PROMPT_VIRAL_EN': '6b9ffef6adf95e89',
    'SYSTEM_PROMPT_VIRAL_LONG': '79ac0e4027bf954d',
    'SYSTEM_PROMPT_VIRAL_LONG_EN': '14d719cd073c86a6',
    'SYSTEM_PROMPT_VIRAL_TRANSLATE': 'e8cba35a7790eb05',
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
    """Tudo o que é congelado mora no pacote de prompts — é essa a fronteira."""
    if not hasattr(prompts, nome):
        raise AssertionError(
            f"{nome} não está em apps.auto_cuts.prompts. Se saiu de propósito, tire a "
            "entrada de HASHES_CONSTANTES no mesmo PR e diga por quê."
        )
    return getattr(prompts, nome)


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
    """A lista congelada e o `__all__` do pacote têm de ser o mesmo conjunto.

    Nos dois sentidos, e cada direção pega um erro diferente:

    - **prompt sem hash** — bastaria adicionar `SYSTEM_PROMPT_NOVO` para ter conteúdo indo
      ao modelo sem nenhum registro de quando mudou.
    - **hash sem prompt** — entrada que sobrou de um prompt removido. Passa despercebida
      para sempre, porque ninguém repara num teste que continua verde.

    Manter as duas listas presas uma à outra é o que evita o congelamento virar decoração.
    """

    def _prompts_expostos(self) -> set[str]:
        return {
            nome
            for nome in getattr(prompts, "__all__", [])
            if isinstance(getattr(prompts, nome), (str, list, set, dict, tuple))
        }

    def test_todo_prompt_publico_tem_hash_congelado(self):
        sem_hash = sorted(self._prompts_expostos() - set(HASHES_CONSTANTES))

        self.assertEqual(
            sem_hash,
            [],
            "Prompt exportado sem hash congelado. Rode o teste, pegue o hash e adicione "
            f"em HASHES_CONSTANTES. Faltando: {sem_hash}",
        )

    def test_nenhum_hash_congelado_sobrou_sem_prompt(self):
        orfaos = sorted(set(HASHES_CONSTANTES) - self._prompts_expostos())

        self.assertEqual(
            orfaos,
            [],
            "Hash congelado sem prompt correspondente em prompts.__all__ — provavelmente "
            f"sobrou de um prompt removido. Tire a entrada. Órfãos: {orfaos}",
        )
