"""Configuração do yt-dlp em `settings` (refactor.md R-17 lote 5 / D-08).

Quinto lote da migração `os.getenv` → `settings`: as 5 variáveis que ajustam o download do
YouTube. Nenhuma delas tinha teste — o arquivo inteiro dependia de variável de ambiente
lida no meio da função, então as quatro funções que montam as opções do yt-dlp eram
intestáveis apesar de serem puras.

São opções de contorno de anti-bot (cookies, player client, runtime JS). Quando o YouTube
aperta, é aqui que se mexe às pressas, em produção — e é o pior lugar possível para não
haver rede de teste.

Também cobre o anti-drift do lote.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.auto_cuts.services.youtube_download import (
    _youtube_format_candidates,
    _yt_dlp_cookie_options,
    _yt_dlp_js_runtime_options,
    _yt_dlp_youtube_extractor_options,
)


class YtdlpSettingsEquivalenceTests(SimpleTestCase):
    """`settings.X` devolve exatamente o que o `os.getenv` devolvia antes."""

    def test_as_quatro_variaveis_de_texto_saem_com_strip(self):
        for nome in (
            "YTDLP_YOUTUBE_PLAYER_CLIENTS",
            "YTDLP_JS_RUNTIMES",
            "YTDLP_COOKIES_FILE",
            "YTDLP_COOKIES_FROM_BROWSER",
        ):
            with self.subTest(variavel=nome):
                self.assertEqual(getattr(settings, nome), (os.getenv(nome) or "").strip())

    def test_altura_minima_tem_default_720_e_piso_zero(self):
        bruto = (os.getenv("YTDLP_MIN_VIDEO_HEIGHT") or "").strip()
        try:
            esperado = int(bruto) if bruto else 720
        except ValueError:
            esperado = 720
        self.assertEqual(settings.YTDLP_MIN_VIDEO_HEIGHT, max(0, esperado))

    @override_settings(YTDLP_MIN_VIDEO_HEIGHT=0)
    def test_altura_zero_desliga_o_filtro(self):
        """`0` é a forma documentada de voltar ao comportamento antigo (melhor disponível)."""
        formatos = _youtube_format_candidates()

        self.assertEqual(formatos[0], "bestvideo+bestaudio/best")
        self.assertFalse(any("height>=" in f for f in formatos))

    @override_settings(YTDLP_MIN_VIDEO_HEIGHT=1080)
    def test_altura_definida_entra_na_frente_sem_perder_os_fallbacks(self):
        """A altura mínima é preferência, não exigência: os fallbacks sem filtro continuam.

        Sem eles, um vídeo que só existe em 480p deixaria de baixar em vez de baixar pior.
        """
        formatos = _youtube_format_candidates()

        self.assertTrue(formatos[0].startswith("bestvideo[height>=1080]"))
        self.assertIn("best", formatos)
        self.assertEqual(len(formatos), len(set(formatos)), "formato repetido na lista")


class PlayerClientTests(SimpleTestCase):
    """Escolha do `player_client`, que é o contorno de anti-bot mais mexido."""

    @override_settings(YTDLP_YOUTUBE_PLAYER_CLIENTS="")
    def test_sem_cookies_usa_android_web_ios(self):
        opts = _yt_dlp_youtube_extractor_options(has_cookies=False)

        self.assertEqual(
            opts["extractor_args"]["youtube"]["player_client"], ["android", "web", "ios"]
        )

    @override_settings(YTDLP_YOUTUBE_PLAYER_CLIENTS="")
    def test_com_cookies_evita_android_e_ios(self):
        """O yt-dlp ignora esses clients quando há cookies — pedi-los é perder a sessão."""
        opts = _yt_dlp_youtube_extractor_options(has_cookies=True)

        clients = opts["extractor_args"]["youtube"]["player_client"]
        self.assertEqual(clients, ["web", "mweb", "tv_embedded"])
        self.assertNotIn("android", clients)
        self.assertNotIn("ios", clients)

    @override_settings(YTDLP_YOUTUBE_PLAYER_CLIENTS="tv_embedded, web")
    def test_lista_explicita_vence_os_dois_defaults(self):
        """É o botão de emergência: com o YouTube apertando, define-se a lista na mão."""
        for tem_cookies in (True, False):
            with self.subTest(cookies=tem_cookies):
                opts = _yt_dlp_youtube_extractor_options(has_cookies=tem_cookies)
                self.assertEqual(
                    opts["extractor_args"]["youtube"]["player_client"], ["tv_embedded", "web"]
                )

    @override_settings(YTDLP_YOUTUBE_PLAYER_CLIENTS=" , ")
    def test_lista_so_com_separadores_nao_vira_lista_vazia_no_yt_dlp(self):
        """Devolve `{}` em vez de `player_client: []`, que faria o yt-dlp falhar sem client."""
        self.assertEqual(_yt_dlp_youtube_extractor_options(has_cookies=False), {})


class JsRuntimeTests(SimpleTestCase):
    @override_settings(YTDLP_JS_RUNTIMES="")
    def test_vazio_deixa_o_default_do_yt_dlp(self):
        """Sem valor, não passa a opção — o yt-dlp usa o Deno dele."""
        self.assertEqual(_yt_dlp_js_runtime_options(), {})

    @override_settings(YTDLP_JS_RUNTIMES="node:/usr/bin/node, deno")
    def test_lista_e_repassada_na_ordem(self):
        self.assertEqual(
            _yt_dlp_js_runtime_options(), {"js_runtimes": ["node:/usr/bin/node", "deno"]}
        )


class CookieOptionTests(SimpleTestCase):
    @override_settings(YTDLP_COOKIES_FILE="", YTDLP_COOKIES_FROM_BROWSER="")
    def test_sem_configuracao_nao_manda_cookie_nenhum(self):
        self.assertEqual(_yt_dlp_cookie_options(), {})

    @override_settings(YTDLP_COOKIES_FILE="/caminho/que/nao/existe.txt")
    def test_arquivo_inexistente_avisa_e_segue_sem_cookies(self):
        """Falhar aqui derrubaria o download inteiro por um caminho errado no `.env`."""
        with self.assertLogs("apps.auto_cuts.services.youtube_download", level="WARNING") as log:
            opts = _yt_dlp_cookie_options()

        self.assertEqual(opts, {})
        self.assertTrue(any("arquivo inexistente" in linha for linha in log.output))

    @override_settings(YTDLP_COOKIES_FILE="", YTDLP_COOKIES_FROM_BROWSER="firefox")
    def test_navegador_sem_perfil_vira_tupla_de_um(self):
        self.assertEqual(_yt_dlp_cookie_options(), {"cookiesfrombrowser": ("firefox",)})

    @override_settings(YTDLP_COOKIES_FILE="", YTDLP_COOKIES_FROM_BROWSER="Chrome:Perfil 2")
    def test_navegador_com_perfil_normaliza_o_nome_mas_preserva_o_perfil(self):
        """O nome do navegador é minúsculo para o yt-dlp; o perfil é do usuário e vai como veio."""
        self.assertEqual(_yt_dlp_cookie_options(), {"cookiesfrombrowser": ("chrome", "Perfil 2")})

    @override_settings(YTDLP_COOKIES_FILE="", YTDLP_COOKIES_FROM_BROWSER=":perfil")
    def test_navegador_sem_nome_e_ignorado(self):
        self.assertEqual(_yt_dlp_cookie_options(), {})


class YtdlpConfigDriftTests(SimpleTestCase):
    """Anti-drift: a etapa (c) do R-17, travada em teste para este lote."""

    def test_nenhum_getenv_de_ytdlp_fora_do_settings(self):
        padrao = re.compile(r'os\.(getenv|environ)\s*[(\[]\s*["\']YTDLP_')

        # O padrão precisa casar com a forma que existia antes do lote 5. Sem esta guarda,
        # uma regex quebrada faria o teste passar vazio para sempre.
        for forma_antiga in (
            '(os.getenv("YTDLP_COOKIES_FILE") or "").strip()',
            "os.environ['YTDLP_MIN_VIDEO_HEIGHT']",
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
            "YTDLP_* lido fora de settings.py — use settings.YTDLP_*. "
            f"Ver R-17/D-08 no refactor.md. Arquivos: {culpados}",
        )
