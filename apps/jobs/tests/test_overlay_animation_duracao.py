"""A saída do overlay tem a duração do vídeo de entrada (FEATURE_PROMPTS_SELECAO_CORTES, PR 8).

O asset animado entra com `-stream_loop -1` — ele precisa se repetir enquanto o clipe durar.
Sem `-map` explícito e sem `-shortest`, quem termina a codificação passa a ser o input que
nunca acaba: medido antes da correção, **1h38 de vídeo gerado em 25 segundos de relógio**,
sem nunca fechar o arquivo. O corte fica com `moov atom not found` e o worker não devolve a
fila. Vale para qualquer asset animado, com ou sem trilha de áudio.

São dois testes de propósito, e o primeiro é o que protege o CI:

  · **forma do comando**, sem executar nada — regressão aqui não pode pendurar a suíte, e é
    exatamente isso que o bug faz;
  · **duração da saída**, com FFmpeg de verdade — o bug é do FFmpeg, e mockar a chamada
    provaria só que os argumentos foram montados.

O segundo é pulado quando não há binário. É a única exceção à regra de zero `skip` deste
projeto, e ela está registrada no plano.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from apps.jobs.services.ffmpeg import CmdResult, overlay_animation

TEM_FFMPEG = bool(shutil.which(settings.FFMPEG_BIN) and shutil.which(settings.FFPROBE_BIN))


class FormaDoComandoTests(SimpleTestCase):
    """Guarda que não executa nada — não há como pendurar a suíte aqui."""

    def comando(self, asset: str = "anim.gif") -> list[str]:
        with patch("apps.jobs.services.ffmpeg.run_cmd", return_value=CmdResult(True, "", "", 0)) as run:
            overlay_animation(Path("base.mp4"), Path("out.mp4"), Path(asset))
        return run.call_args.args[0]

    def test_asset_animado_entra_em_loop_infinito(self):
        """O loop é o comportamento desejado: a animação se repete durante o clipe."""
        self.assertIn("-stream_loop", self.comando("anim.gif"))

    def test_o_comando_limita_a_saida_ao_video_base(self):
        cmd = self.comando("anim.gif")

        self.assertIn("-shortest", cmd)
        self.assertIn("[outv]", cmd)
        self.assertIn("0:a?", cmd)

    def test_o_audio_vem_do_video_base_e_nao_do_asset(self):
        """Sem `-map 0:a?`, a seleção automática pode pegar a trilha do overlay."""
        cmd = self.comando("anim.mp4")
        maps = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-map"]

        self.assertEqual(maps, ["[outv]", "0:a?"])

    def test_asset_estatico_nao_precisa_de_loop(self):
        cmd = self.comando("logo.png")

        self.assertNotIn("-stream_loop", cmd)
        self.assertIn("-shortest", cmd)


class DuracaoRealTests(SimpleTestCase):
    """Com FFmpeg de verdade: o bug é dele, e mock não o pegaria."""

    def gera(self, caminho: Path, segundos: int, *, com_audio: bool) -> None:
        cmd = [settings.FFMPEG_BIN, "-y", "-f", "lavfi",
               "-i", f"color=c=blue:s=160x120:d={segundos}:r=30"]
        if com_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={segundos}"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if com_audio:
            cmd += ["-c:a", "aac", "-shortest"]
        cmd += [str(caminho)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    def duracao(self, caminho: Path) -> float:
        saida = subprocess.run(
            [settings.FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(caminho)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return float(saida.stdout.strip())

    def cenario(self, *, asset_com_audio: bool) -> None:
        if not TEM_FFMPEG:
            self.skipTest("FFmpeg/ffprobe indisponíveis neste ambiente")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            base, asset, saida = tmp / "base.mp4", tmp / "anim.mp4", tmp / "out.mp4"
            self.gera(base, 2, com_audio=True)
            # asset cinco vezes mais longo que o clipe: é o caso que estourava
            self.gera(asset, 10, com_audio=asset_com_audio)

            overlay_animation(base, saida, asset, height=40)

            self.assertAlmostEqual(self.duracao(saida), self.duracao(base), delta=0.2)

    def test_asset_mais_longo_com_audio_nao_estende_a_saida(self):
        self.cenario(asset_com_audio=True)

    def test_asset_mais_longo_sem_audio_nao_estende_a_saida(self):
        self.cenario(asset_com_audio=False)
