"""A métrica rotula pelo modelo que a API respondeu, não pelo que foi pedido.

O provedor redireciona modelo descontinuado sem avisar, e com preço diferente. O próprio
`grok_pricing.py` registra o caso: `grok-4-1-fast` foi descontinuado em maio/2026 e o
destino do redirect, `grok-4.3`, custa 6,25× mais no input e no output.

Rotulando pelo pedido, o painel calculava o custo com a tabela do modelo errado — **barato
demais, exatamente quando o custo real subiu**. O aviso de redirect já existia em
`call_grok_chat`, mas só no log; a métrica seguia mentindo, e é a métrica que alguém olha
para decidir se vale trocar de provedor.

O segundo teste cobre a outra metade do mesmo problema: modelo fora da tabela de preço
produz custo 0,00, que num painel é indistinguível de "barato". Agora avisa.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.auto_cuts.services import grok


class _Filho:
    def __init__(self) -> None:
        self.incs: list[float] = []
        self.observations: list[float] = []

    def inc(self, amount: float = 1) -> None:
        self.incs.append(amount)

    def observe(self, value: float) -> None:
        self.observations.append(value)


class _Metrica:
    def __init__(self) -> None:
        self.children: dict[tuple, _Filho] = {}

    def labels(self, **labels):
        chave = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        return self.children.setdefault(chave, _Filho())

    def modelos(self) -> set[str]:
        return {dict(chave)["model"] for chave in self.children}


def resposta(modelo, *, input_tokens=1000, output_tokens=500):
    usage = SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    return SimpleNamespace(model=modelo, usage=usage)


class RotuloDaMetricaTests(SimpleTestCase):
    def executa(self, *, pedido: str, respondido) -> tuple[_Metrica, _Metrica]:
        requests, cost = _Metrica(), _Metrica()
        cliente = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kw: resposta(respondido))
            )
        )
        with patch.multiple(
            "apps.auto_cuts.services.grok",
            grok_requests_total=requests,
            grok_tokens_total=_Metrica(),
            grok_cost_usd_total=cost,
            grok_request_duration_ms=_Metrica(),
        ):
            grok._execute_grok_chat_completion(
                cliente, model_name=pedido, messages=[], operation="teste"
            )
        return requests, cost

    def test_redirect_do_provedor_aparece_na_metrica(self):
        requests, _ = self.executa(pedido="grok-4-1-fast", respondido="grok-4.3")

        self.assertEqual(requests.modelos(), {"grok-4.3"})

    def test_custo_usa_o_preco_do_modelo_que_respondeu(self):
        """1000 input + 500 output. grok-4.3: 1000×1,25/M + 500×2,50/M = 0,0025."""
        _, cost = self.executa(pedido="grok-4-1-fast", respondido="grok-4.3")

        (filho,) = cost.children.values()
        self.assertAlmostEqual(sum(filho.incs), 0.0025, places=6)

    def test_sem_redirect_o_rotulo_continua_o_mesmo(self):
        requests, _ = self.executa(pedido="grok-4-1-fast", respondido="grok-4-1-fast")

        self.assertEqual(requests.modelos(), {"grok-4-1-fast"})

    def test_resposta_sem_modelo_cai_para_o_pedido(self):
        requests, _ = self.executa(pedido="grok-4-1-fast", respondido=None)

        self.assertEqual(requests.modelos(), {"grok-4-1-fast"})

    def test_resposta_com_modelo_vazio_cai_para_o_pedido(self):
        requests, _ = self.executa(pedido="grok-4-1-fast", respondido="   ")

        self.assertEqual(requests.modelos(), {"grok-4-1-fast"})

    def test_falha_na_chamada_registra_o_modelo_pedido(self):
        """Sem resposta não há modelo real — e a requisição não pode sumir da métrica."""
        requests = _Metrica()

        def explode(**kwargs):
            raise RuntimeError("timeout")

        cliente = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=explode))
        )
        with patch.multiple(
            "apps.auto_cuts.services.grok",
            grok_requests_total=requests,
            grok_tokens_total=_Metrica(),
            grok_cost_usd_total=_Metrica(),
            grok_request_duration_ms=_Metrica(),
        ):
            with self.assertRaises(RuntimeError):
                grok._execute_grok_chat_completion(
                    cliente, model_name="grok-4-1-fast", messages=[], operation="teste"
                )

        self.assertEqual(requests.modelos(), {"grok-4-1-fast"})


class PrecoAusenteTests(SimpleTestCase):
    def setUp(self) -> None:
        grok._warn_missing_pricing_once.cache_clear()

    def test_modelo_fora_da_tabela_avisa_uma_vez(self):
        with self.assertLogs("apps.auto_cuts.services.grok", level="WARNING") as log:
            for _ in range(3):
                custo = grok._calculate_grok_cost_usd(
                    model="modelo-novo-sem-preco",
                    usage={"input_tokens": 1000, "output_tokens": 500},
                )

        self.assertEqual(custo, 0.0)
        avisos = [linha for linha in log.output if "modelo-novo-sem-preco" in linha]
        self.assertEqual(len(avisos), 1, avisos)
        self.assertIn("nao sera contabilizado", avisos[0])

    def test_chamada_sem_uso_de_token_nao_avisa(self):
        """Falha antes de consumir token não é caso de preço faltando."""
        with patch.object(grok.logger, "warning") as aviso:
            grok._calculate_grok_cost_usd(model="modelo-novo-sem-preco", usage={})

        aviso.assert_not_called()

    def test_modelo_conhecido_nao_avisa(self):
        with patch.object(grok.logger, "warning") as aviso:
            grok._calculate_grok_cost_usd(
                model="grok-4.3", usage={"input_tokens": 1000, "output_tokens": 500}
            )

        aviso.assert_not_called()
