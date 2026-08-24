"""Descarte por nota mínima (FEATURE_PROMPTS_SELECAO_CORTES, PR 6).

O filtro é a peça que dá consequência à calibração de nota do PR 4: sem ele, um candidato
honestamente pontuado com 12 entra na entrega do mesmo jeito, desde que não haja dez
melhores. Com ele, `AUTO_CUT_MIN_VIRALITY_SCORE` vira a régua do que vale virar vídeo.

Vem desligado por default (`0`), e é assim que este PR entra em produção sem mudar nada.

Duas decisões ficam presas aqui, porque as duas são fáceis de "corrigir" na direção errada
depois:

  · **item sem nota não é descartado** — nota ausente é "não avaliado", não "zero". Tratar
    como zero transformaria falha de formato da resposta em descarte silencioso;
  · **zerar a entrega não é erro** — vídeo cujo melhor momento não passa da régua gera zero
    corte, com mensagem explicando. Virar `status="error"` faria a factory tratar conteúdo
    fraco como falha de sistema, e alguém iria "consertar" baixando a régua.
"""

from __future__ import annotations

from django.test import TestCase, override_settings

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutSuggestion
from apps.auto_cuts.services.analysis_flow import _create_suggestions


def short(start: str, end: str, score) -> dict:
    return {
        "start_timestamp": start,
        "end_timestamp": end,
        "virality_score": score,
        "suggested_title": "Título 🎯",
        "theme_category": "COMEDY_HUMOR",
    }


def longo(start: str, end: str, score) -> dict:
    return {
        "start_timestamp": start,
        "end_timestamp": end,
        "virality_score": score,
        "title_suggestion": "Título do longo 🎯",
        "theme_category": "COMEDY_HUMOR",
    }


class FiltroDeScoreTests(TestCase):
    def criar(self, *, shorts=None, longs=None, analysis=None) -> AutoCutAnalysis:
        analysis = analysis or AutoCutAnalysis.objects.create(status="analyzing")
        _create_suggestions(
            analysis,
            {
                "candidate_shorts": shorts or [],
                "ranked_shorts": [],
                "final_long_cuts": longs or [],
            },
            "viral",
        )
        return analysis

    def notas(self, analysis, cut_type="short") -> list[int | None]:
        return [
            s.virality_score
            for s in AutoCutSuggestion.objects.filter(
                analysis=analysis, cut_type=cut_type
            ).order_by("rank", "id")
        ]

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=70)
    def test_descarta_abaixo_do_limiar_e_mantem_o_limiar_exato(self):
        """CA-12: 70 passa, 69 não — o limiar é inclusivo."""
        analysis = self.criar(
            shorts=[
                short("10:00", "10:40", 90),
                short("11:00", "11:40", 71),
                short("12:00", "12:40", 70),
                short("13:00", "13:40", 69),
            ]
        )

        self.assertEqual(self.notas(analysis), [90, 71, 70])

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=0)
    def test_limiar_zero_nao_descarta_nada(self):
        """CA-13: o default. É o que faz este PR entrar sem mudar produção."""
        analysis = self.criar(
            shorts=[short("10:00", "10:40", 90), short("11:00", "11:40", 3)]
        )

        self.assertEqual(self.notas(analysis), [90, 3])

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=70)
    def test_item_sem_nota_nao_e_descartado(self):
        """CA-14: nota ausente é "não avaliado", não zero."""
        analysis = self.criar(
            shorts=[short("10:00", "10:40", None), short("11:00", "11:40", 30)]
        )

        self.assertEqual(self.notas(analysis), [None])

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=70)
    def test_item_com_nota_ilegivel_nao_e_descartado(self):
        analysis = self.criar(shorts=[short("10:00", "10:40", "alta")])

        self.assertEqual(self.notas(analysis), [None])

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=70)
    def test_o_filtro_vale_para_cortes_longos_tambem(self):
        analysis = self.criar(
            longs=[longo("00:00", "10:00", 90), longo("20:00", "30:00", 40)]
        )

        self.assertEqual(self.notas(analysis, "long"), [90])

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=95)
    def test_zerar_a_entrega_nao_vira_erro_e_explica_na_mensagem(self):
        """CA-15: vídeo fraco gera zero corte, não falha de sistema."""
        analysis = AutoCutAnalysis.objects.create(status="analyzing")

        with self.assertLogs("apps.auto_cuts.services.analysis_flow", level="WARNING") as log:
            self.criar(
                analysis=analysis,
                shorts=[short("10:00", "10:40", 60), short("11:00", "11:40", 50)],
                longs=[longo("00:00", "10:00", 40)],
            )

        self.assertEqual(self.notas(analysis), [])
        self.assertEqual(self.notas(analysis, "long"), [])
        self.assertNotEqual(analysis.status, "error")
        self.assertIn("95", analysis.progress_message)
        self.assertIn("3 candidato(s)", analysis.progress_message)
        self.assertTrue(
            any("nenhum corte acima da nota mínima" in linha.lower() for linha in log.output),
            log.output,
        )

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=95)
    def test_entrega_vazia_por_outro_motivo_nao_gera_a_mensagem_do_filtro(self):
        """Short abaixo de 30s é descartado por duração, e a mensagem não pode culpar a nota."""
        analysis = AutoCutAnalysis.objects.create(status="analyzing")
        self.criar(analysis=analysis, shorts=[short("10:00", "10:20", 99)])

        self.assertEqual(self.notas(analysis), [])
        self.assertEqual(analysis.progress_message, "")

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=70)
    def test_mensagem_nao_aparece_quando_sobrou_corte(self):
        analysis = AutoCutAnalysis.objects.create(status="analyzing")
        self.criar(
            analysis=analysis,
            shorts=[short("10:00", "10:40", 90), short("11:00", "11:40", 20)],
        )

        self.assertEqual(self.notas(analysis), [90])
        self.assertEqual(analysis.progress_message, "")

    @override_settings(AUTO_CUT_MIN_VIRALITY_SCORE=70)
    def test_a_mensagem_cabe_no_campo(self):
        """`progress_message` é CharField(max_length=200)."""
        analysis = AutoCutAnalysis.objects.create(status="analyzing")
        self.criar(analysis=analysis, shorts=[short("10:00", "10:40", 10)])

        self.assertLessEqual(len(analysis.progress_message), 200)
