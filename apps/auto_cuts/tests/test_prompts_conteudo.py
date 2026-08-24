"""O que os prompts precisam DIZER (FEATURE_PROMPTS_SELECAO_CORTES).

`test_grok_prompts_integridade.py` congela o texto: prova que nada mudou sem passar pela
revisão. Este arquivo é a outra metade — prova que certas regras **estão lá**, em todos os
prompts que precisam delas.

A diferença importa quando um prompt novo nasce. O hash pega "mudou sem avisar"; só o hash
não pega "nasceu sem a regra". Um `SYSTEM_PROMPT_VIRAL_2026` copiado do viral atual sem o
bloco de sinais do autor passaria no congelamento (é constante nova, ganha hash novo) e
chegaria à produção sem a regra. Aqui não passa: a lista de prompts é derivada de
`prompts.__all__`, e prompt não classificado quebra o teste.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.auto_cuts import prompts

# Os 7 prompts de análise, por idioma. `viral_translate` é derivado do `viral_en` e por isso
# é EN aqui, mesmo produzindo legenda em português.
SYSTEM_PROMPTS_PT = [
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_VIRAL_LONG",
    "SYSTEM_PROMPT_EDUCATIONAL",
]
SYSTEM_PROMPTS_EN = [
    "SYSTEM_PROMPT_VIRAL_EN",
    "SYSTEM_PROMPT_VIRAL_LONG_EN",
    "SYSTEM_PROMPT_EDUCATIONAL_EN",
    "SYSTEM_PROMPT_VIRAL_TRANSLATE",
]
TEMPLATES = [nome for nome in prompts.__all__ if nome.startswith("CHUNKS_PROMPT_TEMPLATE")]


class CoberturaDaListaDePromptsTests(SimpleTestCase):
    """A lista deste arquivo e o `__all__` do pacote têm de descrever o mesmo conjunto."""

    def test_todo_system_prompt_de_analise_esta_classificado_por_idioma(self):
        expostos = {
            nome
            for nome in prompts.__all__
            if nome.startswith("SYSTEM_PROMPT")
        }
        classificados = set(SYSTEM_PROMPTS_PT) | set(SYSTEM_PROMPTS_EN)
        self.assertEqual(
            expostos,
            classificados,
            "Prompt de análise novo (ou removido) sem atualizar as listas deste arquivo. "
            "Classifique-o por idioma no mesmo PR — é o que garante que ele nasça com as "
            "regras que todos os outros já têm.",
        )

    def test_os_sete_templates_estao_na_lista(self):
        self.assertEqual(len(TEMPLATES), 7, TEMPLATES)


class SinaisDoAutorTests(SimpleTestCase):
    """PR 2 · RN-03, RN-04, RN-05, RN-06."""

    def test_todo_system_prompt_pt_traz_o_bloco_no_idioma_certo(self):
        for nome in SYSTEM_PROMPTS_PT:
            with self.subTest(prompt=nome):
                texto = getattr(prompts, nome)
                self.assertIn(prompts.AUTHOR_CUE_RULES_PT, texto)
                self.assertNotIn(prompts.AUTHOR_CUE_RULES_EN, texto)

    def test_todo_system_prompt_en_traz_o_bloco_no_idioma_certo(self):
        for nome in SYSTEM_PROMPTS_EN:
            with self.subTest(prompt=nome):
                texto = getattr(prompts, nome)
                self.assertIn(prompts.AUTHOR_CUE_RULES_EN, texto)
                self.assertNotIn(prompts.AUTHOR_CUE_RULES_PT, texto)

    def test_as_duas_direcoes_do_marcador_estao_na_regra(self):
        """RN-04: cortar para o lado errado entrega o aviso sem a explicação."""
        self.assertIn("PARA FRENTE", prompts.AUTHOR_CUE_RULES_PT)
        self.assertIn("PARA TRÁS", prompts.AUTHOR_CUE_RULES_PT)
        self.assertIn("FORWARD", prompts.AUTHOR_CUE_RULES_EN)
        self.assertIn("BACKWARD", prompts.AUTHOR_CUE_RULES_EN)

    def test_a_regra_proibe_o_marcador_de_vazar_para_titulo_e_hook(self):
        """RN-05: o marcador diz onde cortar, não o que dizer."""
        for bloco in (prompts.AUTHOR_CUE_RULES_PT, prompts.AUTHOR_CUE_RULES_EN):
            with self.subTest(bloco=bloco[:30]):
                for campo in ("suggested_title", "thumbnail_text", "hook_sentence"):
                    self.assertIn(campo, bloco)

    def test_a_regra_impede_o_marcador_de_salvar_trecho_fraco(self):
        """Sem isto, a regra vira um caminho para nota inflada em qualquer trecho marcado."""
        self.assertIn("não salva trecho fraco", prompts.AUTHOR_CUE_RULES_PT)
        self.assertIn("does not rescue a weak passage", prompts.AUTHOR_CUE_RULES_EN)

    def test_todo_template_declara_author_cue(self):
        for nome in TEMPLATES:
            with self.subTest(template=nome):
                self.assertIn("author_cue", getattr(prompts, nome))

    def test_o_limite_de_120_caracteres_esta_declarado_nos_dois_idiomas(self):
        self.assertIn("120", prompts.AUTHOR_CUE_RULES_PT)
        self.assertIn("120", prompts.AUTHOR_CUE_RULES_EN)


class EscalaDaNotaTests(SimpleTestCase):
    """PR 3 · RN-07: uma escala só, 0–100, em todos os modos de análise."""

    def test_nenhum_prompt_de_analise_pede_a_escala_de_1_a_10(self):
        """O backend não reescalona: escala divergente vira nota incomparável no banco."""
        for nome in SYSTEM_PROMPTS_PT + SYSTEM_PROMPTS_EN + TEMPLATES:
            with self.subTest(prompt=nome):
                texto = getattr(prompts, nome)
                self.assertNotIn("1–10", texto)
                self.assertNotIn("1-10", texto)

    def test_os_dois_prompts_educacionais_pedem_0_a_100(self):
        for nome in ("SYSTEM_PROMPT_EDUCATIONAL", "SYSTEM_PROMPT_EDUCATIONAL_EN"):
            with self.subTest(prompt=nome):
                self.assertIn("0–100", getattr(prompts, nome))

    def test_o_prompt_de_corte_pronto_segue_em_1_a_10_de_proposito(self):
        """Ele não passa por `_create_suggestions`; o backend converte ×10 explicitamente
        em `analysis_flow._process_ready_cuts_flow`. Mudar a escala aqui sem mexer lá
        multiplicaria a nota por 10 de novo."""
        self.assertIn("1-10", prompts.READY_CUT_SYSTEM_PROMPT_BASE)


class CalibracaoDaNotaTests(SimpleTestCase):
    """PR 4 · RN-08: a nota precisa significar algo em termos absolutos.

    Sem âncora, `virality_score` é ordenação relativa e nada mais — e o prompt ainda manda
    "retorne EXATAMENTE N itens", o que empurra o modelo a inflar para preencher a cota. O
    filtro do PR 6 depende de 70 querer dizer a mesma coisa em vídeo bom e em vídeo ruim.
    """

    FAIXAS = ("85–100", "70–84", "50–69", "0–49")

    def test_todo_system_prompt_pt_traz_a_calibracao_no_idioma_certo(self):
        for nome in SYSTEM_PROMPTS_PT:
            with self.subTest(prompt=nome):
                texto = getattr(prompts, nome)
                self.assertIn(prompts.SCORE_CALIBRATION_RULES_PT, texto)
                self.assertNotIn(prompts.SCORE_CALIBRATION_RULES_EN, texto)

    def test_todo_system_prompt_en_traz_a_calibracao_no_idioma_certo(self):
        for nome in SYSTEM_PROMPTS_EN:
            with self.subTest(prompt=nome):
                texto = getattr(prompts, nome)
                self.assertIn(prompts.SCORE_CALIBRATION_RULES_EN, texto)
                self.assertNotIn(prompts.SCORE_CALIBRATION_RULES_PT, texto)

    def test_as_quatro_faixas_estao_declaradas_nos_dois_idiomas(self):
        for bloco in (prompts.SCORE_CALIBRATION_RULES_PT, prompts.SCORE_CALIBRATION_RULES_EN):
            for faixa in self.FAIXAS:
                with self.subTest(faixa=faixa):
                    self.assertIn(faixa, bloco)

    def test_a_regra_autoriza_explicitamente_nota_abaixo_de_50(self):
        """A parte que muda o resultado: sem permissão, o modelo trata nota baixa como erro."""
        self.assertIn("abaixo de 50", prompts.SCORE_CALIBRATION_RULES_PT)
        self.assertIn("below 50", prompts.SCORE_CALIBRATION_RULES_EN)

    def test_a_regra_proibe_inflar_nota_para_preencher_a_cota(self):
        self.assertIn("preencher a quantidade pedida", prompts.SCORE_CALIBRATION_RULES_PT)
        self.assertIn("fill the requested count", prompts.SCORE_CALIBRATION_RULES_EN)

    def test_a_calibracao_cobre_o_criterio_educacional(self):
        """As faixas falam de prender atenção; no educacional o critério é ensinar."""
        self.assertIn("ensina", prompts.SCORE_CALIBRATION_RULES_PT)
        self.assertIn("teaches", prompts.SCORE_CALIBRATION_RULES_EN)
