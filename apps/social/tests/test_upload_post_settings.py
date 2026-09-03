"""Configuração do Upload-Post em `settings` (refactor.md R-17 lote 4 / D-08).

Quarto lote da migração `os.getenv` → `settings`. Este é o caso que o plano usou como
exemplo do problema: os três parâmetros de throttle do cliente de analytics eram
**constantes de módulo**, calculadas no import. Congeladas ali, nem `override_settings`
nem `patch.dict(os.environ)` alcançavam — e por isso o cooldown, que é o mecanismo que
protege a integração contra bloqueio de borda, nunca teve teste.

O arquivo cobre:

  1. **Equivalência** — cada setting devolve o que o `os.getenv` devolvia, com o mesmo
     default e a mesma (não-)normalização.
  2. **O ramo que a migração destravou** — throttle e cooldown, agora configuráveis por
     teste.
  3. **Anti-drift** — nenhum `os.getenv("UPLOAD_POST_*")` fora de `settings.py`.

⚠ A chave da API fica **crua** no settings, sem `.strip()`. O publisher usava o valor como
veio; só o acessor do cliente de analytics normalizava. Unificar mudaria o que o publisher
manda no header — se for para arrumar, é item próprio, não carona de refatoração.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.social.services import upload_post_analytics_client as client


class UploadPostSettingsEquivalenceTests(SimpleTestCase):
    """`settings.X` devolve exatamente o que o `os.getenv` devolvia antes."""

    def test_chave_da_api_fica_crua(self):
        self.assertEqual(settings.UPLOAD_POST_API_KEY, os.getenv("UPLOAD_POST_API_KEY") or "")

    def test_acessor_do_cliente_e_quem_normaliza(self):
        """O `.strip()` mora no acessor, não na setting — era assim antes."""
        with override_settings(UPLOAD_POST_API_KEY="  chave-com-espaco  "):
            self.assertEqual(client.get_upload_post_api_key(), "chave-com-espaco")

    def test_chave_ausente_vira_string_vazia_e_nao_none(self):
        """`_headers()` compara com falsy e o publisher levanta erro legível a partir daí."""
        with override_settings(UPLOAD_POST_API_KEY=""):
            self.assertEqual(client.get_upload_post_api_key(), "")

    def test_parametros_de_throttle_mantem_os_defaults(self):
        casos = [
            ("UPLOAD_POST_ANALYTICS_MIN_INTERVAL_SEC", "0.6"),
            ("UPLOAD_POST_ANALYTICS_COOLDOWN_SEC", "30"),
            ("UPLOAD_POST_ANALYTICS_MAX_WAIT_SEC", "5"),
            ("UPLOAD_POST_FACTORY_BRAND_DELAY_SEC", "0.15"),
        ]
        for nome, padrao in casos:
            with self.subTest(variavel=nome):
                self.assertEqual(getattr(settings, nome), float(os.getenv(nome, padrao)))


class ThrottleTests(SimpleTestCase):
    """O ramo que estava intestável: throttle e cooldown do cliente de analytics.

    O estado do throttle é global no módulo (é um limitador de processo, não de request),
    então cada teste zera antes e depois — senão um caso arma o cooldown e o seguinte
    herda a espera.
    """

    def setUp(self):
        super().setUp()
        self._zerar()
        self.addCleanup(self._zerar)

    def _zerar(self):
        client._last_request_mono = 0.0
        client._cooldown_until_mono = 0.0

    @override_settings(
        UPLOAD_POST_ANALYTICS_MIN_INTERVAL_SEC=0.0,
        UPLOAD_POST_ANALYTICS_MAX_WAIT_SEC=5.0,
    )
    def test_sem_intervalo_minimo_a_chamada_passa_direto(self):
        ok, espera = client._throttle_upload_post()

        self.assertTrue(ok)
        self.assertEqual(espera, 0.0)

    @override_settings(
        UPLOAD_POST_ANALYTICS_MIN_INTERVAL_SEC=0.0,
        UPLOAD_POST_ANALYTICS_MAX_WAIT_SEC=5.0,
    )
    def test_cooldown_curto_e_esperado_dentro_da_propria_chamada(self):
        """Espera abaixo do teto: o cliente segura a request e prossegue."""
        client._cooldown_until_mono = time.monotonic() + 0.01

        ok, espera = client._throttle_upload_post()

        self.assertTrue(ok)
        self.assertGreater(espera, 0.0)

    @override_settings(
        UPLOAD_POST_ANALYTICS_MIN_INTERVAL_SEC=0.0,
        UPLOAD_POST_ANALYTICS_MAX_WAIT_SEC=5.0,
    )
    def test_cooldown_longo_aborta_em_vez_de_bloquear(self):
        """Acima do teto, devolve `ok=False` **sem dormir**.

        É o ponto do desenho: preferir erro imediato de rate limit a prender a request do
        usuário por 30 segundos.
        """
        client._cooldown_until_mono = time.monotonic() + 30

        inicio = time.monotonic()
        ok, espera = client._throttle_upload_post()
        decorrido = time.monotonic() - inicio

        self.assertFalse(ok)
        self.assertGreater(espera, 5.0)
        self.assertLess(decorrido, 1.0, "abortou, mas dormiu — o teto não foi respeitado")

    @override_settings(UPLOAD_POST_ANALYTICS_COOLDOWN_SEC=30.0)
    def test_trip_cooldown_arma_a_pausa_global(self):
        client._trip_cooldown("429")

        self.assertGreater(client._cooldown_until_mono, time.monotonic())

    @override_settings(UPLOAD_POST_ANALYTICS_COOLDOWN_SEC=0.0)
    def test_cooldown_zerado_desliga_o_mecanismo(self):
        """`0` é a forma documentada de desligar a pausa global."""
        client._trip_cooldown("429")

        self.assertEqual(client._cooldown_until_mono, 0.0)

    @override_settings(UPLOAD_POST_ANALYTICS_COOLDOWN_SEC=30.0)
    def test_cooldown_ja_armado_nao_e_encurtado_por_uma_falha_nova(self):
        """Duas falhas seguidas não podem reduzir a pausa que já estava valendo."""
        alvo_distante = time.monotonic() + 120
        client._cooldown_until_mono = alvo_distante

        client._trip_cooldown("outro 429")

        self.assertEqual(client._cooldown_until_mono, alvo_distante)


class UploadPostConfigDriftTests(SimpleTestCase):
    """Anti-drift: a etapa (c) do R-17, travada em teste para este lote."""

    def test_nenhum_getenv_de_upload_post_fora_do_settings(self):
        padrao = re.compile(r'os\.(getenv|environ)\s*[(\[]\s*["\']UPLOAD_POST_')

        # O padrão precisa casar com a forma que existia antes do lote 4. Sem esta guarda,
        # uma regex quebrada faria o teste passar vazio para sempre.
        for forma_antiga in (
            'os.getenv("UPLOAD_POST_API_KEY")',
            'float(os.getenv("UPLOAD_POST_ANALYTICS_COOLDOWN_SEC", "30"))',
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
            "UPLOAD_POST_* lido fora de settings.py — use settings.X. "
            f"Ver R-17/D-08 no refactor.md. Arquivos: {culpados}",
        )
