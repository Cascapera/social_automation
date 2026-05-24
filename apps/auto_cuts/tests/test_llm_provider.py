"""Testes para configuração de provider LLM."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.auto_cuts.services.grok import LLM_PROVIDER_DEFAULTS, _build_llm_client


class BuildLlmClientProviderTests(SimpleTestCase):
    """_build_llm_client resolve base_url corretamente para cada provider."""

    def _run(self, env: dict):
        captured = {}

        def fake_openai(*, api_key, base_url):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            return MagicMock()

        with patch.dict("os.environ", env, clear=False):
            with patch("apps.auto_cuts.services.grok.OpenAI", side_effect=fake_openai):
                # OpenAI is imported inside _build_llm_client via `from openai import OpenAI`
                # so we patch at the module level
                pass

        # Patch the import inside the function

        original = None
        try:
            import openai as _openai_mod
            original = _openai_mod.OpenAI
            _openai_mod.OpenAI = fake_openai
            with patch.dict("os.environ", env, clear=False):
                _build_llm_client(light=False)
        finally:
            if original is not None:
                _openai_mod.OpenAI = original

        return captured

    def _base_url_for(self, env: dict, light: bool = False) -> str:
        captured_url = {}

        class FakeClient:
            pass

        def fake_openai(api_key, base_url):
            captured_url["base_url"] = base_url
            return FakeClient()

        import openai as _openai_mod
        original = _openai_mod.OpenAI
        try:
            _openai_mod.OpenAI = fake_openai
            with patch.dict("os.environ", {"LLM_API_KEY": "test-key", **env}, clear=False):
                _build_llm_client(light=light)
        finally:
            _openai_mod.OpenAI = original

        return captured_url.get("base_url", "")

    def test_xai_provider_uses_xai_base_url(self):
        url = self._base_url_for({"LLM_PROVIDER": "xai", "LLM_BASE_URL": ""})
        self.assertEqual(url, LLM_PROVIDER_DEFAULTS["xai"])

    def test_google_provider_uses_gemini_base_url(self):
        url = self._base_url_for({"LLM_PROVIDER": "google", "LLM_BASE_URL": ""})
        self.assertEqual(url, LLM_PROVIDER_DEFAULTS["google"])

    def test_openai_provider_uses_openai_base_url(self):
        url = self._base_url_for({"LLM_PROVIDER": "openai", "LLM_BASE_URL": ""})
        self.assertEqual(url, LLM_PROVIDER_DEFAULTS["openai"])

    def test_explicit_llm_base_url_overrides_provider_default(self):
        custom = "https://my-proxy.example.com/v1"
        url = self._base_url_for({"LLM_PROVIDER": "xai", "LLM_BASE_URL": custom})
        self.assertEqual(url, custom)

    def test_unknown_provider_falls_back_to_xai(self):
        url = self._base_url_for({"LLM_PROVIDER": "unknownprovider", "LLM_BASE_URL": ""})
        self.assertEqual(url, LLM_PROVIDER_DEFAULTS["xai"])


class BuildLlmClientApiKeyFallbackTests(SimpleTestCase):
    """Fallback de XAI_API_KEY quando LLM_API_KEY está ausente."""

    def _client_and_model(self, env: dict, light: bool = False):
        received = {}

        def fake_openai(api_key, base_url):
            received["api_key"] = api_key
            return MagicMock()

        import openai as _openai_mod
        original = _openai_mod.OpenAI
        model_out = [None]
        try:
            _openai_mod.OpenAI = fake_openai
            clean_env = {k: "" for k in ("LLM_API_KEY", "XAI_API_KEY", "LLM_MODEL", "GROK_MODEL", "LLM_BASE_URL")}
            clean_env.update(env)
            with patch.dict("os.environ", clean_env, clear=False):
                _, model, _ = _build_llm_client(light=light)
                model_out[0] = model
        finally:
            _openai_mod.OpenAI = original

        return received.get("api_key"), model_out[0]

    def test_llm_api_key_used_when_set(self):
        api_key, _ = self._client_and_model({"LLM_API_KEY": "llm-key-123"})
        self.assertEqual(api_key, "llm-key-123")

    def test_fallback_to_xai_api_key_when_llm_api_key_absent(self):
        api_key, _ = self._client_and_model({"LLM_API_KEY": "", "XAI_API_KEY": "xai-legacy-key"})
        self.assertEqual(api_key, "xai-legacy-key")

    def test_fallback_to_xai_api_key_emits_warning(self):
        with self.assertLogs("apps.auto_cuts.services.grok", level="WARNING") as cm:
            import openai as _openai_mod
            original = _openai_mod.OpenAI
            try:
                _openai_mod.OpenAI = lambda api_key, base_url: MagicMock()
                with patch.dict(
                    "os.environ",
                    {"LLM_API_KEY": "", "XAI_API_KEY": "xai-key", "LLM_BASE_URL": ""},
                    clear=False,
                ):
                    _build_llm_client()
            finally:
                _openai_mod.OpenAI = original
        self.assertTrue(any("XAI_API_KEY deprecated" in line for line in cm.output))

    def test_missing_api_key_raises(self):
        import openai as _openai_mod
        original = _openai_mod.OpenAI
        try:
            _openai_mod.OpenAI = lambda api_key, base_url: MagicMock()
            with patch.dict("os.environ", {"LLM_API_KEY": "", "XAI_API_KEY": ""}, clear=False):
                with self.assertRaises(ValueError, msg="LLM_API_KEY não configurada"):
                    _build_llm_client()
        finally:
            _openai_mod.OpenAI = original

    def test_light_model_uses_llm_model_light(self):
        _, model = self._client_and_model(
            {"LLM_API_KEY": "k", "LLM_MODEL": "gpt-4o", "LLM_MODEL_LIGHT": "gpt-4o-mini"},
            light=True,
        )
        self.assertEqual(model, "gpt-4o-mini")

    def test_heavy_model_uses_llm_model(self):
        _, model = self._client_and_model(
            {"LLM_API_KEY": "k", "LLM_MODEL": "gpt-4o", "LLM_MODEL_LIGHT": "gpt-4o-mini"},
            light=False,
        )
        self.assertEqual(model, "gpt-4o")

    def test_grok_model_fallback_emits_warning(self):
        with self.assertLogs("apps.auto_cuts.services.grok", level="WARNING") as cm:
            import openai as _openai_mod
            original = _openai_mod.OpenAI
            try:
                _openai_mod.OpenAI = lambda api_key, base_url: MagicMock()
                with patch.dict(
                    "os.environ",
                    {"LLM_API_KEY": "k", "LLM_MODEL": "", "GROK_MODEL": "grok-legacy", "LLM_BASE_URL": ""},
                    clear=False,
                ):
                    _build_llm_client()
            finally:
                _openai_mod.OpenAI = original
        self.assertTrue(any("GROK_MODEL deprecated" in line for line in cm.output))


