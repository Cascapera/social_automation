"""Últimas configurações em `settings` (refactor.md R-17 lote 6 / D-08).

Sexto e último lote da migração `os.getenv` → `settings`: o OAuth de Contas, a chave da
YouTube Data API, o limite de páginas da varredura de canal, a chave de criptografia dos
segredos em banco e a URL do frontend.

O lote nasceu de um achado do lote 5: **`youtube_oauth.py` lia `GOOGLE_CLIENT_ID` por
`os.getenv` embora o lote 1 já a tivesse posto em `settings`** — e `youtube_credentials.py`,
ao lado, já lia de `settings`. Duas fontes para a mesma credencial, no mesmo fluxo. O
anti-drift do lote 1 só guardava `YOUTUBE_CHECK_*`, então a duplicata passou. Daí os
anti-drift deste arquivo cobrirem nome por nome.

Além da equivalência, aqui ficam os ramos que a migração destravou — com destaque para o
contrato entre `secret_crypto` e a API: a mensagem de erro da chave ausente é comparada por
substring em `apps/api/views.py` para virar um 400 legível. Se o texto mudar, o usuário
volta a ver erro 500 genérico, e nenhum teste pegaria isso sem o caso abaixo.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.social.services.secret_crypto import (
    ENV_KEY_NAME,
    decrypt_secret,
    encrypt_secret,
)
from apps.social.services.youtube_oauth import get_client_config, get_redirect_uri
from apps.social.views import _frontend_url


class EquivalenceTests(SimpleTestCase):
    """`settings.X` devolve exatamente o que o `os.getenv` devolvia antes."""

    def test_redirect_do_oauth_de_contas_tem_default_de_dev(self):
        esperado = os.getenv("YOUTUBE_REDIRECT_URI", "http://localhost:8000/api/youtube/callback/")
        self.assertEqual(settings.YOUTUBE_REDIRECT_URI, esperado)

    def test_redirect_de_contas_e_diferente_do_de_factory_check(self):
        """São dois callbacks distintos. Reaproveitar um no outro quebra o fluxo em
        produção sem erro visível no código — o lote 1 já registrava isso."""
        self.assertNotEqual(settings.YOUTUBE_REDIRECT_URI, settings.YOUTUBE_CHECK_REDIRECT_URI)

    def test_chaves_da_api_saem_com_strip(self):
        for nome in ("YOUTUBE_API_KEY", "GOOGLE_API_KEY", "SOCIAL_ENCRYPTION_KEY"):
            with self.subTest(variavel=nome):
                self.assertEqual(getattr(settings, nome), (os.getenv(nome) or "").strip())

    def test_paginas_da_varredura_ficam_entre_1_e_12(self):
        bruto = os.getenv("YOUTUBE_FULL_SCAN_MAX_PAGES", "4") or "4"
        self.assertEqual(settings.YOUTUBE_FULL_SCAN_MAX_PAGES, max(1, min(12, int(bruto))))
        self.assertGreaterEqual(settings.YOUTUBE_FULL_SCAN_MAX_PAGES, 1)
        self.assertLessEqual(settings.YOUTUBE_FULL_SCAN_MAX_PAGES, 12)

    def test_frontend_url_tem_default_de_dev(self):
        self.assertEqual(settings.FRONTEND_URL, os.getenv("FRONTEND_URL", "http://localhost:5173"))


class FrontendRedirectTests(SimpleTestCase):
    """O redirect pós-OAuth, que era `os.getenv` dentro da função."""

    @override_settings(FRONTEND_URL="https://app.exemplo.com")
    def test_monta_a_url_com_o_caminho(self):
        self.assertEqual(_frontend_url("/contas"), "https://app.exemplo.com/contas")

    @override_settings(FRONTEND_URL="https://app.exemplo.com/")
    def test_barra_final_na_configuracao_nao_vira_barra_dupla(self):
        """Barra sobrando no `.env` é o erro de digitação mais comum aqui."""
        self.assertEqual(_frontend_url("/contas"), "https://app.exemplo.com/contas")


class OAuthClientConfigTests(SimpleTestCase):
    """`get_client_config` sem credencial em banco: o caminho do `.env`.

    Era intestável — lia `os.getenv` no meio da função.
    """

    @override_settings(GOOGLE_CLIENT_ID=None, GOOGLE_CLIENT_SECRET=None)
    def test_sem_credencial_devolve_none(self):
        self.assertIsNone(get_client_config())

    @override_settings(GOOGLE_CLIENT_ID="id-123", GOOGLE_CLIENT_SECRET=None)
    def test_meia_credencial_e_o_mesmo_que_nenhuma(self):
        self.assertIsNone(get_client_config())

    @override_settings(
        GOOGLE_CLIENT_ID="id-123",
        GOOGLE_CLIENT_SECRET="segredo",
        YOUTUBE_REDIRECT_URI="https://app.exemplo.com/api/youtube/callback/",
    )
    def test_com_credencial_monta_a_config_web(self):
        config = get_client_config()

        self.assertEqual(config["web"]["client_id"], "id-123")
        self.assertEqual(config["web"]["client_secret"], "segredo")
        self.assertEqual(
            config["web"]["redirect_uris"], ["https://app.exemplo.com/api/youtube/callback/"]
        )
        self.assertEqual(config["web"]["token_uri"], "https://oauth2.googleapis.com/token")

    @override_settings(YOUTUBE_REDIRECT_URI="https://app.exemplo.com/api/youtube/callback/")
    def test_redirect_sem_fonte_cai_na_configuracao(self):
        self.assertEqual(get_redirect_uri(), "https://app.exemplo.com/api/youtube/callback/")


class SecretCryptoTests(SimpleTestCase):
    """A chave de criptografia dos segredos em banco."""

    @override_settings(SOCIAL_ENCRYPTION_KEY="chave-de-teste-bem-comprida-123")
    def test_ida_e_volta_preserva_o_segredo(self):
        cifrado = encrypt_secret("client-secret-do-google")

        self.assertNotEqual(cifrado, "client-secret-do-google")
        self.assertTrue(cifrado.startswith("enc:v1:"))
        self.assertEqual(decrypt_secret(cifrado), "client-secret-do-google")

    @override_settings(SOCIAL_ENCRYPTION_KEY="chave-de-teste-bem-comprida-123")
    def test_valor_ja_cifrado_nao_e_cifrado_de_novo(self):
        """Sem isso, um save repetido empilharia camadas e o decrypt devolveria lixo."""
        uma_vez = encrypt_secret("segredo")

        self.assertEqual(encrypt_secret(uma_vez), uma_vez)

    @override_settings(SOCIAL_ENCRYPTION_KEY="")
    def test_sem_chave_a_mensagem_de_erro_cita_o_nome_da_variavel(self):
        """⚠ Contrato com a API: `apps/api/views.py` compara `"SOCIAL_ENCRYPTION_KEY" in
        str(exc)` para transformar isto num 400 legível. Mudar o texto devolve o usuário
        a um 500 genérico."""
        with self.assertRaises(ValueError) as ctx:
            encrypt_secret("segredo")

        self.assertIn(ENV_KEY_NAME, str(ctx.exception))
        self.assertIn("SOCIAL_ENCRYPTION_KEY", str(ctx.exception))

    @override_settings(SOCIAL_ENCRYPTION_KEY="chave-de-teste-bem-comprida-123")
    def test_valor_vazio_nao_chega_a_usar_a_chave(self):
        self.assertEqual(encrypt_secret(""), "")


class ConfigDriftTests(SimpleTestCase):
    """Anti-drift do lote 6 — e a lição do achado que originou o lote."""

    def test_nenhum_getenv_das_variaveis_deste_lote_fora_do_settings(self):
        padrao = re.compile(
            r'os\.(getenv|environ)\s*[(\[]\s*["\']'
            r"(GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET|GOOGLE_API_KEY|YOUTUBE_API_KEY"
            r"|YOUTUBE_REDIRECT_URI|YOUTUBE_FULL_SCAN_MAX_PAGES|SOCIAL_ENCRYPTION_KEY"
            r'|FRONTEND_URL)["\']'
        )

        # O padrão precisa casar com as formas que existiam antes do lote 6. Sem esta
        # guarda, uma regex quebrada faria o teste passar vazio para sempre.
        for forma_antiga in (
            'os.getenv("GOOGLE_CLIENT_ID")',
            'os.getenv("YOUTUBE_REDIRECT_URI", "http://localhost:8000/api/youtube/callback/")',
            'os.getenv("FRONTEND_URL", "http://localhost:5173")',
            "os.environ['SOCIAL_ENCRYPTION_KEY']",
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
            "Configuração lida fora de settings.py — use settings.X. "
            f"Ver R-17/D-08 no refactor.md. Arquivos: {culpados}",
        )

    def test_a_chave_lida_por_nome_indireto_tambem_esta_coberta(self):
        """`secret_crypto` fazia `os.getenv(ENV_KEY_NAME)` — o nome vinha de uma constante.

        Nenhuma regex sobre o literal pegaria isso. A guarda aqui é diferente: a constante
        continua existindo (a mensagem de erro depende dela), mas ninguém pode voltar a
        usá-la para ler o ambiente.
        """
        origem = (
            Path(__file__).resolve().parents[1] / "services" / "secret_crypto.py"
        ).read_text(encoding="utf-8")

        self.assertNotRegex(origem, r"os\.(getenv|environ)")
        self.assertIn("settings.SOCIAL_ENCRYPTION_KEY", origem)
