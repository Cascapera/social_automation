"""Configuração do Whisper em `settings` (refactor.md R-17 lote 2 / D-08).

Segundo lote da migração `os.getenv` → `settings`. Este cobre `WHISPER_MODEL`,
`WHISPER_DEVICE` e `WHISPER_DEBUG_GPU`, lidos em 5 arquivos: os dois pontos de carga do
modelo em `jobs/services/subtitles.py`, o rótulo de workload em `jobs/tasks.py`, a
transcrição fatiada de `auto_cuts` e a do Multiple-Creator.

Como no lote 1, o arquivo cobre as duas metades da migração:

  1. **Equivalência** — cada setting devolve exatamente o que o `os.getenv` devolvia, com
     o mesmo default. É a mitigação que o R-17 pede: errar um default silenciosamente muda
     comportamento em produção sem quebrar teste nenhum.
  2. **O ramo que a migração destravou** — `_whisper_workload_type()` decidia entre CPU e
     GPU lendo a env no meio da função. Agora responde a `override_settings`, então as
     três combinações cabem em teste.

⚠ **`WHISPER_MODEL` tem dois defaults, e isso é de propósito.** A transcrição fatiada usa
`small`; a de passada única usa `large-v3`. Uma setting só apagaria a diferença — e o
efeito em produção seria silencioso: vídeo longo passaria a carregar um modelo 10× maior
em cada bloco, ou o curto perderia qualidade. Por isso são duas settings sobre a mesma
variável de ambiente. Unificar é decisão de qualidade × custo, não de refatoração.

E um anti-drift: nenhum `os.getenv("WHISPER_*")` pode voltar a aparecer fora de
`settings.py` — a etapa (c) do R-17 travada em teste para este lote.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.jobs.tasks import _whisper_workload_type


class WhisperSettingsEquivalenceTests(SimpleTestCase):
    """`settings.X` devolve exatamente o que o `os.getenv` devolvia antes."""

    def test_modelo_da_passada_unica_cai_em_large_v3(self):
        esperado = (os.getenv("WHISPER_MODEL", "") or "").strip() or "large-v3"
        self.assertEqual(settings.WHISPER_MODEL_FULL, esperado)

    def test_modelo_da_transcricao_fatiada_cai_em_small(self):
        esperado = (os.getenv("WHISPER_MODEL", "") or "").strip() or "small"
        self.assertEqual(settings.WHISPER_MODEL_CHUNKED, esperado)

    def test_a_variavel_de_ambiente_atras_das_duas_settings_e_a_mesma(self):
        """Com `WHISPER_MODEL` preenchida, as duas settings convergem.

        A divergência é só de default. Quem define a variável no ambiente escolhe um
        modelo para os dois caminhos — e continua sendo assim depois da migração.
        """
        if not (os.getenv("WHISPER_MODEL", "") or "").strip():
            self.assertNotEqual(
                settings.WHISPER_MODEL_FULL,
                settings.WHISPER_MODEL_CHUNKED,
                "sem WHISPER_MODEL no ambiente, os defaults precisam continuar diferentes",
            )
        else:
            self.assertEqual(settings.WHISPER_MODEL_FULL, settings.WHISPER_MODEL_CHUNKED)

    def test_device_sai_normalizado_em_minusculas(self):
        """Os três leitores faziam `.strip().lower()`. Sem isso, `WHISPER_DEVICE=CPU`
        deixaria de forçar CPU e o job iria para a GPU que deveria estar livre."""
        self.assertEqual(settings.WHISPER_DEVICE, os.getenv("WHISPER_DEVICE", "").strip().lower())

    def test_debug_gpu_continua_sensivel_a_maiuscula(self):
        """`.strip()` sem `.lower()`, como era.

        `WHISPER_DEBUG_GPU=TRUE` não ligava o debug antes e continua não ligando. Passar a
        aceitar mudaria o volume de log em produção sem ninguém pedir — se for para
        arrumar, é item próprio.
        """
        self.assertEqual(
            settings.WHISPER_DEBUG_GPU,
            os.getenv("WHISPER_DEBUG_GPU", "").strip() in ("1", "true", "yes"),
        )
        with override_settings(WHISPER_DEBUG_GPU=False):
            self.assertFalse(settings.WHISPER_DEBUG_GPU)


class WhisperWorkloadTypeTests(SimpleTestCase):
    """O ramo que estava intestável até este lote.

    O rótulo `cpu`/`gpu` vai para as métricas de transcrição. Errar aqui não quebra o
    pipeline: só faz o painel atribuir o tempo ao hardware errado, que é o tipo de bug que
    ninguém percebe.
    """

    @override_settings(WHISPER_FORCE_CPU=False, WHISPER_DEVICE="")
    def test_sem_forcar_nada_o_rotulo_e_gpu(self):
        self.assertEqual(_whisper_workload_type(), "gpu")

    @override_settings(WHISPER_FORCE_CPU=False, WHISPER_DEVICE="cpu")
    def test_device_cpu_sozinho_ja_forca_cpu(self):
        """A env manda mesmo com `WHISPER_FORCE_CPU` desligado."""
        self.assertEqual(_whisper_workload_type(), "cpu")

    @override_settings(WHISPER_FORCE_CPU=True, WHISPER_DEVICE="")
    def test_force_cpu_sozinho_ja_forca_cpu(self):
        """É o default do projeto: Whisper na CPU para deixar a GPU livre para o NVENC."""
        self.assertEqual(_whisper_workload_type(), "cpu")

    @override_settings(WHISPER_FORCE_CPU=False, WHISPER_DEVICE="cuda")
    def test_device_cuda_nao_e_confundido_com_cpu(self):
        """Contraprova: só a string exata `cpu` força CPU."""
        self.assertEqual(_whisper_workload_type(), "gpu")


class WhisperConfigDriftTests(SimpleTestCase):
    """Anti-drift: a etapa (c) do R-17, travada em teste para este lote."""

    def test_nenhum_getenv_de_whisper_fora_do_settings(self):
        """Um `os.getenv("WHISPER_...")` novo desfaz a migração em silêncio.

        Foi assim que a configuração se espalhou por 18 arquivos: cada leitor resolveu a
        própria leitura na hora em que precisou, e nenhuma revisão pegou porque cada
        ocorrência isolada parece inofensiva. Foi assim, também, que `WHISPER_MODEL` ganhou
        dois defaults diferentes sem que ninguém decidisse isso.
        """
        padrao = re.compile(r'os\.(getenv|environ)\s*[(\[]\s*["\']WHISPER_')

        # O padrão precisa casar com a forma que existia antes do lote 2. Sem esta guarda,
        # uma regex quebrada faria o teste passar vazio para sempre.
        for forma_antiga in (
            'os.getenv("WHISPER_MODEL", "small").strip() or "small"',
            "os.environ['WHISPER_DEVICE']",
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
            "WHISPER_* lido fora de settings.py — use settings.WHISPER_*. "
            f"Ver R-17/D-08 no refactor.md. Arquivos: {culpados}",
        )
