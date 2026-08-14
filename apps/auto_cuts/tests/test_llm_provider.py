"""Configuração do cliente LLM (refactor.md R-17 lote 3 / D-08).

Estes testes existiam antes do R-17 e afirmavam as mesmas coisas — precedência de chave,
de modelo e de base URL. O que mudou foi **como** a configuração entra no teste:
`_build_llm_client` lia `os.getenv` no meio da função, então cada caso precisava de
`patch.dict("os.environ", ...)` e de um truque para trocar `openai.OpenAI` na marra.

Com a leitura em `settings`, `override_settings` resolve a metade da configuração e o
arquivo encolhe. As asserções são as mesmas.

A precedência **continua em `grok.py`**, não no settings: é lá que os avisos de
depreciação de `XAI_API_KEY` e `GROK_MODEL` são emitidos, uma vez por chamada. Mover a
decisão para o settings emitiria o aviso uma vez só, no boot, onde ninguém lê.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.auto_cuts.services.grok import LLM_PROVIDER_DEFAULTS, _build_llm_client

# Configuração "limpa": todo fallback desligado, para cada teste ligar só o que precisa.
SEM_CONFIG = {
    "LLM_PROVIDER": "xai",
    "LLM_API_KEY": "",
    "XAI_API_KEY": "",
    "LLM_MODEL": "",
    "LLM_MODEL_LIGHT": "",
    "GROK_MODEL": "",
    "LLM_BASE_URL": "",
}


class LlmClientTestCase(SimpleTestCase):
    """Base: captura o que chegou ao construtor do cliente OpenAI, sem rede."""

    def build(self, light: bool = False, **overrides):
        """Roda `_build_llm_client` e devolve `(api_key, base_url, model, provider)`."""
        recebido = {}

        def fake_openai(api_key, base_url):
            recebido["api_key"] = api_key
            recebido["base_url"] = base_url
            return MagicMock()

        # `_build_llm_client` faz `from openai import OpenAI` dentro da função, então o
        # patch precisa ser no módulo de origem, não no namespace do grok.
        with patch("openai.OpenAI", side_effect=fake_openai):
            with override_settings(**{**SEM_CONFIG, **overrides}):
                _, model, provider = _build_llm_client(light=light)

        return recebido.get("api_key"), recebido.get("base_url"), model, provider


class BaseUrlPorProviderTests(LlmClientTestCase):
    """`LLM_BASE_URL` explícita ganha; sem ela, vale o padrão do provider."""

    def test_xai_usa_a_base_url_da_xai(self):
        _, base_url, _, _ = self.build(LLM_API_KEY="k", LLM_PROVIDER="xai")
        self.assertEqual(base_url, LLM_PROVIDER_DEFAULTS["xai"])

    def test_google_usa_a_base_url_do_gemini(self):
        _, base_url, _, _ = self.build(LLM_API_KEY="k", LLM_PROVIDER="google")
        self.assertEqual(base_url, LLM_PROVIDER_DEFAULTS["google"])

    def test_openai_usa_a_base_url_da_openai(self):
        _, base_url, _, _ = self.build(LLM_API_KEY="k", LLM_PROVIDER="openai")
        self.assertEqual(base_url, LLM_PROVIDER_DEFAULTS["openai"])

    def test_base_url_explicita_vence_o_padrao_do_provider(self):
        custom = "https://my-proxy.example.com/v1"
        _, base_url, _, _ = self.build(LLM_API_KEY="k", LLM_PROVIDER="xai", LLM_BASE_URL=custom)
        self.assertEqual(base_url, custom)

    def test_provider_desconhecido_cai_na_xai(self):
        """Errar o nome do provider não pode virar `base_url` vazia e erro de conexão."""
        _, base_url, _, provider = self.build(LLM_API_KEY="k", LLM_PROVIDER="unknownprovider")
        self.assertEqual(base_url, LLM_PROVIDER_DEFAULTS["xai"])
        self.assertEqual(provider, "unknownprovider")


class PrecedenciaDaChaveTests(LlmClientTestCase):
    """`LLM_API_KEY` > `XAI_API_KEY` (depreciada)."""

    def test_llm_api_key_e_usada_quando_existe(self):
        api_key, _, _, _ = self.build(LLM_API_KEY="llm-key-123")
        self.assertEqual(api_key, "llm-key-123")

    def test_cai_na_xai_api_key_quando_a_nova_falta(self):
        api_key, _, _, _ = self.build(LLM_API_KEY="", XAI_API_KEY="xai-legacy-key")
        self.assertEqual(api_key, "xai-legacy-key")

    def test_o_fallback_avisa_no_log(self):
        """O aviso é o único sinal de que a instalação está em configuração legada."""
        with self.assertLogs("apps.auto_cuts.services.grok", level="WARNING") as log:
            self.build(LLM_API_KEY="", XAI_API_KEY="xai-key")
        self.assertTrue(any("XAI_API_KEY deprecated" in linha for linha in log.output))

    def test_sem_nenhuma_chave_levanta(self):
        """Falhar aqui é melhor que montar um cliente que erra 401 na primeira chamada."""
        with self.assertRaises(ValueError):
            self.build(LLM_API_KEY="", XAI_API_KEY="")


class PrecedenciaDoModeloTests(LlmClientTestCase):
    """`LLM_MODEL_LIGHT`/`LLM_MODEL` > `GROK_MODEL` (depreciada) > `grok-4-1-fast`."""

    def test_chamada_leve_usa_o_modelo_leve(self):
        _, _, model, _ = self.build(
            light=True, LLM_API_KEY="k", LLM_MODEL="gpt-4o", LLM_MODEL_LIGHT="gpt-4o-mini"
        )
        self.assertEqual(model, "gpt-4o-mini")

    def test_chamada_pesada_usa_o_modelo_principal(self):
        _, _, model, _ = self.build(
            light=False, LLM_API_KEY="k", LLM_MODEL="gpt-4o", LLM_MODEL_LIGHT="gpt-4o-mini"
        )
        self.assertEqual(model, "gpt-4o")

    def test_grok_model_avisa_no_log(self):
        with self.assertLogs("apps.auto_cuts.services.grok", level="WARNING") as log:
            self.build(LLM_API_KEY="k", LLM_MODEL="", GROK_MODEL="grok-legacy")
        self.assertTrue(any("GROK_MODEL deprecated" in linha for linha in log.output))

    def test_sem_modelo_nenhum_cai_no_default_do_codigo(self):
        """O default não está em `settings` de propósito: é fallback de última linha.

        Subi-lo para a configuração faria parecer que alguém escolheu `grok-4-1-fast` para
        esta instalação — e ninguém escolheu.
        """
        _, _, model, _ = self.build(LLM_API_KEY="k")
        self.assertEqual(model, "grok-4-1-fast")

    def test_chamada_leve_sem_modelo_leve_cai_no_grok_model(self):
        """Contraprova de precedência: `LLM_MODEL` NÃO cobre a chamada leve.

        Com `LLM_MODEL_LIGHT` vazia, o caminho leve ignora `LLM_MODEL` e vai direto para o
        fallback depreciado. É o comportamento de hoje; se um dia ele mudar, que seja de
        propósito.
        """
        _, _, model, _ = self.build(
            light=True, LLM_API_KEY="k", LLM_MODEL="gpt-4o", GROK_MODEL="grok-legacy"
        )
        self.assertEqual(model, "grok-legacy")
