"""Configuração do cliente de check do YouTube em `settings` (refactor.md R-17 / D-08).

O D-08 mediu 57 `os.getenv` fora de `settings.py`, em 18 arquivos. O problema não é
estético: lido no import, o valor congela antes de qualquer teste rodar, e nem
`override_settings` nem `patch.dict(os.environ)` conseguem mudá-lo depois. O caminho fica
intestável — e era o caso deste lote, o das credenciais `YOUTUBE_CHECK_*`.

Este arquivo cobre as duas metades da migração:

  1. **Equivalência** — cada variável migrada continua lendo a MESMA env var, com o MESMO
     default. É a mitigação que o R-17 pede no plano: errar um default silenciosamente
     muda comportamento em produção sem quebrar teste nenhum.
  2. **O ramo que a migração destravou** — `get_check_client_config()` agora responde a
     `override_settings`, então dá para testar ligado e desligado. Antes não dava.

E um anti-drift: nenhum `os.getenv("YOUTUBE_CHECK_*")` pode voltar a aparecer fora de
`settings.py`. É a etapa (c) do R-17 travada em teste para este lote — sem ela, a
migração se desfaz sozinha no próximo arquivo que precisar da variável.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.social.services.youtube_oauth import get_check_client_config

REDIRECT_URI_PADRAO = "http://127.0.0.1:8000/api/youtube/factory-check-callback/"


class YouTubeCheckSettingsEquivalenceTests(SimpleTestCase):
    """`settings.X` devolve exatamente o que o `os.getenv` devolvia antes."""

    def test_client_id_e_secret_saem_da_env_ja_normalizados(self):
        """Os três leitores repetiam `(os.getenv(...) or "").strip()`.

        A normalização subiu para o settings — se ela sumisse, uma variável preenchida
        só com espaço passaria a contar como configurada e o OAuth tentaria autenticar
        com credencial em branco.
        """
        for nome in ("YOUTUBE_CHECK_CLIENT_ID", "YOUTUBE_CHECK_CLIENT_SECRET"):
            with self.subTest(variavel=nome):
                self.assertEqual(
                    getattr(settings, nome), (os.getenv(nome) or "").strip()
                )

    def test_redirect_uri_cai_no_default_do_factory_check(self):
        """Default preservado do `youtube_oauth.get_check_client_config`.

        É o callback do factory-check, deliberadamente diferente do `YOUTUBE_REDIRECT_URI`
        usado no OAuth de Contas — trocar um pelo outro quebra o fluxo em produção sem
        erro visível no código.
        """
        esperado = (os.getenv("YOUTUBE_CHECK_REDIRECT_URI") or "").strip() or REDIRECT_URI_PADRAO
        self.assertEqual(settings.YOUTUBE_CHECK_REDIRECT_URI, esperado)

    def test_fallback_global_do_google_continua_podendo_ser_none(self):
        """`GOOGLE_CLIENT_ID` NÃO é normalizado para "" de propósito.

        `youtube_credentials` faz `source_client_id or settings.GOOGLE_CLIENT_ID` e depois
        checa `if not client_id`. Tanto `None` quanto `""` são falsy ali, mas manter o
        `None` original evita que uma variável ausente vire string vazia e mude o valor
        que chega ao `Credentials(...)` do Google.
        """
        for nome in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
            with self.subTest(variavel=nome):
                self.assertEqual(getattr(settings, nome), os.getenv(nome))


class GetCheckClientConfigTests(SimpleTestCase):
    """O ramo que estava intestável até o R-17.

    `get_check_client_config` lia `os.getenv` direto. Com a leitura em `settings`, os dois
    estados — cliente de check ligado e desligado — passam a caber num teste.
    """

    @override_settings(YOUTUBE_CHECK_CLIENT_ID="", YOUTUBE_CHECK_CLIENT_SECRET="")
    def test_sem_credencial_devolve_none(self):
        self.assertIsNone(get_check_client_config())

    @override_settings(YOUTUBE_CHECK_CLIENT_ID="id-123", YOUTUBE_CHECK_CLIENT_SECRET="")
    def test_secret_faltando_devolve_none(self):
        """Meia credencial é o mesmo que nenhuma — não pode virar config parcial."""
        self.assertIsNone(get_check_client_config())

    @override_settings(YOUTUBE_CHECK_CLIENT_ID="", YOUTUBE_CHECK_CLIENT_SECRET="segredo")
    def test_id_faltando_devolve_none(self):
        self.assertIsNone(get_check_client_config())

    @override_settings(
        YOUTUBE_CHECK_CLIENT_ID="id-123",
        YOUTUBE_CHECK_CLIENT_SECRET="segredo",
        YOUTUBE_CHECK_REDIRECT_URI=REDIRECT_URI_PADRAO,
    )
    def test_com_credencial_monta_a_config_web(self):
        config = get_check_client_config()

        self.assertEqual(config["web"]["client_id"], "id-123")
        self.assertEqual(config["web"]["client_secret"], "segredo")
        self.assertEqual(config["web"]["redirect_uris"], [REDIRECT_URI_PADRAO])
        self.assertEqual(config["web"]["token_uri"], "https://oauth2.googleapis.com/token")
        self.assertEqual(config["redirect_uri"], REDIRECT_URI_PADRAO)

    @override_settings(
        YOUTUBE_CHECK_CLIENT_ID="id-123",
        YOUTUBE_CHECK_CLIENT_SECRET="segredo",
        YOUTUBE_CHECK_REDIRECT_URI="https://app.exemplo.com/api/youtube/factory-check-callback/",
    )
    def test_redirect_uri_customizado_chega_nos_dois_lugares(self):
        """O redirect vai no `redirect_uris` do web E na chave de topo — os dois são lidos."""
        config = get_check_client_config()

        esperado = "https://app.exemplo.com/api/youtube/factory-check-callback/"
        self.assertEqual(config["web"]["redirect_uris"], [esperado])
        self.assertEqual(config["redirect_uri"], esperado)


class YouTubeCheckConfigDriftTests(SimpleTestCase):
    """Anti-drift: a etapa (c) do R-17, travada em teste para este lote."""

    def test_nenhum_getenv_de_youtube_check_fora_do_settings(self):
        """Um `os.getenv("YOUTUBE_CHECK_...")` novo desfaz a migração em silêncio.

        Foi assim que a configuração se espalhou por 18 arquivos: cada um resolveu a
        própria leitura na hora em que precisou, e nenhuma revisão pegou porque cada
        ocorrência isolada parece inofensiva.
        """
        padrao = re.compile(r'os\.(getenv|environ)\s*[(\[]\s*["\']YOUTUBE_CHECK_')

        # O padrão precisa casar com a forma que existia antes do R-17. Sem esta guarda,
        # uma regex quebrada faria o teste passar vazio para sempre.
        for forma_antiga in (
            '(os.getenv("YOUTUBE_CHECK_CLIENT_ID") or "").strip()',
            "os.environ['YOUTUBE_CHECK_CLIENT_SECRET']",
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
            "YOUTUBE_CHECK_* lido fora de settings.py — use settings.YOUTUBE_CHECK_*. "
            f"Ver R-17/D-08 no refactor.md. Arquivos: {culpados}",
        )
