"""Quantos candidatos são pedidos ao LLM (FEATURE_PROMPTS_SELECAO_CORTES, PR 5).

Até aqui o número era fixo em `LLM_MAX_SHORTS`/`LLM_MAX_LONGS`, os mesmos 10 e 5 para todo
job. Isso errava nos dois sentidos: job com alvo 3 pagava output de 10 candidatos que nunca
seriam usados, e job com alvo 12 recebia 10 — dos quais o backend ainda descartava por
duração, por categoria sem brand mapeada e (a partir do PR 6) por nota.

Agora a quantidade sai do alvo do job vezes `LLM_CANDIDATE_MARGIN`, limitada pelo teto. Os
testes abaixo cobrem os dois lados dessa conta e o efeito dela na validação da resposta —
pedir menos não pode fazer a resposta ser recusada por "abaixo do mínimo".
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.auto_cuts.models import AutoCutAnalysis
from apps.auto_cuts.services.analysis_flow import (
    _candidates_to_request,
    _delivery_limits,
    _request_llm_analysis,
)


class QuantidadePedidaTests(TestCase):
    """RN-09: `ceil(alvo × margem)`, limitado pelo teto de env."""

    def analise(self, **campos) -> AutoCutAnalysis:
        return AutoCutAnalysis.objects.create(status="analyzing", **campos)

    @override_settings(LLM_CANDIDATE_MARGIN=1.5, LLM_MAX_SHORTS=20, LLM_MAX_LONGS=10)
    def test_alvo_pequeno_pede_pouco(self):
        """O caso que hoje desperdiça: alvo 4 pedia 10 candidatos completos."""
        analysis = self.analise(shorts_target=4, longs_target=2)

        self.assertEqual(_candidates_to_request(analysis), (6, 3))

    @override_settings(LLM_CANDIDATE_MARGIN=1.5, LLM_MAX_SHORTS=20, LLM_MAX_LONGS=10)
    def test_fracao_arredonda_para_cima(self):
        """3 × 1,5 = 4,5. Arredondar para baixo devolveria margem menor que a pedida."""
        analysis = self.analise(shorts_target=3, longs_target=3)

        self.assertEqual(_candidates_to_request(analysis), (5, 5))

    @override_settings(LLM_CANDIDATE_MARGIN=1.5, LLM_MAX_SHORTS=15, LLM_MAX_LONGS=8)
    def test_o_teto_de_env_limita_a_margem(self):
        """Alvo 12 entrega 10 (teto da entrega), 10 × 1,5 = 15, e o teto de env é 15."""
        analysis = self.analise(shorts_target=12, longs_target=5)

        self.assertEqual(_candidates_to_request(analysis), (15, 8))

    @override_settings(LLM_CANDIDATE_MARGIN=1.5, LLM_MAX_SHORTS=10, LLM_MAX_LONGS=5)
    def test_com_o_env_de_hoje_o_teto_absorve_a_margem_de_job_grande(self):
        """Sem subir o `.env`, job de alvo 10 continua pedindo 10 — comportamento de hoje.

        É o que faz este PR entrar em produção sem mudar nada para os jobs grandes: a
        margem só passa a valer quando `LLM_MAX_SHORTS` subir.
        """
        analysis = self.analise(shorts_target=10, longs_target=5)

        self.assertEqual(_candidates_to_request(analysis), (10, 5))

    @override_settings(LLM_CANDIDATE_MARGIN=1.0, LLM_MAX_SHORTS=20, LLM_MAX_LONGS=10)
    def test_margem_1_desliga_a_folga(self):
        analysis = self.analise(shorts_target=6, longs_target=4)

        self.assertEqual(_candidates_to_request(analysis), (6, 4))

    def test_alvo_acima_do_teto_de_entrega_nao_infla_o_pedido(self):
        """O campo aceita 30, a entrega para em 10: pedir 45 seria output jogado fora."""
        analysis = self.analise(shorts_target=30, longs_target=10)

        self.assertEqual(_delivery_limits(analysis), (10, 5))


class PedidoChegaAoClienteTests(TestCase):
    """A quantidade calculada tem de chegar em `analyze_chunks_in_one_request`."""

    @override_settings(LLM_CANDIDATE_MARGIN=1.5, LLM_MAX_SHORTS=20, LLM_MAX_LONGS=10)
    def test_request_llm_analysis_passa_a_quantidade_calculada(self):
        analysis = AutoCutAnalysis.objects.create(
            status="analyzing", shorts_target=4, longs_target=2
        )
        alvo = "apps.auto_cuts.services.analysis_flow.analyze_chunks_in_one_request"

        with patch(alvo, return_value={"candidate_shorts": [], "final_long_cuts": []}) as chamada:
            _request_llm_analysis(analysis, [{"text": "bloco", "start_sec": 0, "end_sec": 60}])

        self.assertEqual(chamada.call_args.kwargs["max_shorts"], 6)
        self.assertEqual(chamada.call_args.kwargs["max_longs"], 3)


class MinimoDaValidacaoTests(SimpleTestCase):
    """RN-10: o mínimo exigido sai da quantidade pedida, não do env.

    Sem isto, pedir 6 candidatos e receber 6 seria recusado por "abaixo do mínimo" quando o
    env estivesse em 20 — e cada recusa reenvia a transcrição inteira, três vezes.
    """

    @override_settings(LLM_MAX_SHORTS=20, LLM_MAX_LONGS=10)
    def test_resposta_do_tamanho_pedido_e_aceita(self):
        from apps.auto_cuts.services import grok

        payload = {
            "candidate_shorts": [{"theme_category": "COMEDY_HUMOR"} for _ in range(6)],
            "ranked_shorts": [],
            "final_long_cuts": [{"theme_category": "COMEDY_HUMOR"} for _ in range(3)],
        }

        with patch.object(grok, "call_grok_chat", return_value="{}") as chat:
            chat.return_value = __import__("json").dumps(payload)
            resultado = grok.analyze_chunks_in_one_request(
                [{"text": "bloco", "start_sec": 0, "end_sec": 60}],
                max_shorts=6,
                max_longs=3,
            )

        self.assertEqual(len(resultado["candidate_shorts"]), 6)

    @override_settings(LLM_MAX_SHORTS=20, LLM_MAX_LONGS=10)
    def test_o_prompt_pede_a_quantidade_do_chamador_nao_a_do_env(self):
        from apps.auto_cuts.services import grok

        payload = {
            "candidate_shorts": [{"theme_category": "COMEDY_HUMOR"} for _ in range(6)],
            "ranked_shorts": [],
            "final_long_cuts": [{"theme_category": "COMEDY_HUMOR"} for _ in range(3)],
        }

        with patch.object(grok, "call_grok_chat") as chat:
            chat.return_value = __import__("json").dumps(payload)
            grok.analyze_chunks_in_one_request(
                [{"text": "bloco", "start_sec": 0, "end_sec": 60}],
                max_shorts=6,
                max_longs=3,
            )

        user = chat.call_args.args[1]
        self.assertIn("EXATAMENTE 6", user)
        self.assertIn("EXATAMENTE 3", user)
        self.assertNotIn("EXATAMENTE 20", user)
