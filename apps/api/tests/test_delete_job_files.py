"""Limpeza de arquivos ao apagar um job de cortes (refactor.md R-20 / D-10).

`_delete_auto_cut_job_files` é a maior concentração de exceção engolida do projeto: eram
**18 `except Exception: pass`** numa função só, apagando o vídeo original, os chunks do
lote, os cortes, as thumbnails, as sobras por glob e o diretório de processamento.

A tolerância é correta — apagar um job não pode parar porque um arquivo resistiu. O que
não era correto é ninguém ficar sabendo: cada falha deixa um arquivo no disco sem registro
nenhum, e o disco de mídia é o recurso que mais cresce neste projeto.

Estes testes não existiam. Eles travam as duas metades: **o que a função apaga** (para a
reescrita não ter deixado nada para trás) e **o que ela registra quando não consegue**.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from apps.api.views.auto_cuts import _delete_auto_cut_job_files
from apps.auto_cuts.models import (
    AutoCutAnalysis,
    AutoCutCorte,
    AutoCutReadyChunk,
    AutoCutSuggestion,
)
from apps.brands.models import Brand, Factory

LOGGER_CLEANUP = "apps.jobs.services.media_cleanup"
LOGGER_VIEW = "apps.api.views.auto_cuts"


class DeleteJobFilesTests(TestCase):
    def setUp(self):
        super().setUp()
        factory = Factory.objects.create(name="Factory DEL")
        self.brand = Brand.objects.create(name="Brand DEL", slug="brand-del", factory=factory)
        self.analysis = AutoCutAnalysis.objects.create(brand=self.brand, status="done")

    def com_video_original(self):
        self.analysis.file.save("original.mp4", ContentFile(b"video"), save=True)
        return Path(self.analysis.file.path)

    def com_corte(self, *, thumb=True):
        sug = AutoCutSuggestion.objects.create(
            analysis=self.analysis, cut_type="short", start_tc="00:10", end_tc="00:40"
        )
        corte = AutoCutCorte.objects.create(
            analysis=self.analysis, suggestion=sug, format="vertical"
        )
        corte.file.save("corte.mp4", ContentFile(b"corte"), save=True)
        if thumb:
            corte.thumbnail.save("corte.jpg", ContentFile(b"thumb"), save=True)
        return corte

    def com_chunk_do_lote(self):
        chunk = AutoCutReadyChunk.objects.create(analysis=self.analysis, order_index=0)
        chunk.file.save("chunk.mp4", ContentFile(b"chunk"), save=True)
        return Path(chunk.file.path)

    def test_apaga_video_original_corte_thumbnail_e_chunk(self):
        """O caminho feliz — é o que garante que a reescrita não esqueceu nenhum arquivo."""
        original = self.com_video_original()
        corte = self.com_corte()
        chunk = self.com_chunk_do_lote()
        corte_path = Path(corte.file.path)
        thumb_path = Path(corte.thumbnail.path)
        for caminho in (original, corte_path, thumb_path, chunk):
            self.assertTrue(caminho.exists(), f"fixture não criou {caminho}")

        _delete_auto_cut_job_files(self.analysis)

        for caminho in (original, corte_path, thumb_path, chunk):
            self.assertFalse(caminho.exists(), f"ficou no disco: {caminho}")

    def test_job_sem_arquivo_nenhum_nao_levanta_e_nao_loga(self):
        """Job criado e apagado sem nunca ter mídia é caso normal, não falha."""
        with patch("apps.jobs.services.media_cleanup.log_event") as evento:
            _delete_auto_cut_job_files(self.analysis)
        evento.assert_not_called()

    def test_falha_ao_apagar_o_video_nao_impede_a_limpeza_do_corte(self):
        """A tolerância é o ponto: um arquivo travado não pode salvar os outros."""
        self.com_video_original()
        corte = self.com_corte(thumb=False)
        corte_path = Path(corte.file.path)

        original_delete = type(self.analysis.file).delete

        def falha_so_no_original(self_campo, save=True):
            if "original" in (self_campo.name or ""):
                raise OSError("arquivo em uso")
            return original_delete(self_campo, save=save)

        with patch.object(type(self.analysis.file), "delete", falha_so_no_original):
            with self.assertLogs(LOGGER_CLEANUP, level="ERROR") as log:
                _delete_auto_cut_job_files(self.analysis)

        self.assertIn("delete_job_source", "\n".join(log.output))
        self.assertFalse(corte_path.exists(), "o corte devia ter sido apagado mesmo assim")

    def test_o_evento_diz_de_qual_job_e_de_qual_corte(self):
        """Sem os ids, o log diz que sobrou arquivo mas não de quem."""
        corte = self.com_corte(thumb=False)

        with patch(
            "django.db.models.fields.files.FieldFile.delete",
            side_effect=OSError("disco cheio"),
        ), patch("pathlib.Path.unlink", side_effect=OSError("disco cheio")):
            with self.assertLogs(LOGGER_CLEANUP, level="ERROR") as log:
                _delete_auto_cut_job_files(self.analysis)

        linha = "\n".join(log.output)
        self.assertIn("delete_job_cut", linha)
        self.assertIn(f'"analysis_id": {self.analysis.id}', linha)
        self.assertIn(f'"corte_id": {corte.id}', linha)

    def test_falha_na_consulta_dos_chunks_nao_derruba_o_resto(self):
        """A consulta também era engolida. Continua sendo tolerada — agora com registro."""
        # Guardado antes: depois do delete o campo fica vazio e `.path` levanta.
        original = self.com_video_original()

        with patch(
            "apps.auto_cuts.models.AutoCutReadyChunk.objects.filter",
            side_effect=RuntimeError("banco fora"),
        ):
            with self.assertLogs(LOGGER_VIEW, level="ERROR") as log:
                _delete_auto_cut_job_files(self.analysis)

        self.assertIn("delete_job_ready_chunk_query", "\n".join(log.output))
        self.assertFalse(original.exists(), "o vídeo original devia ter sido apagado mesmo assim")

    @override_settings()
    def test_diretorio_de_processamento_e_removido(self):
        """`cortes_processo/<id>` sobra quando a transcrição fatiada morre no meio."""
        from django.conf import settings

        chunks_dir = Path(settings.MEDIA_ROOT) / "cortes_processo" / str(self.analysis.id)
        chunks_dir.mkdir(parents=True, exist_ok=True)
        (chunks_dir / "bloco.wav").write_bytes(b"audio")

        _delete_auto_cut_job_files(self.analysis)

        self.assertFalse(chunks_dir.exists())

    def test_falha_ao_remover_o_diretorio_vira_evento(self):
        from django.conf import settings

        chunks_dir = Path(settings.MEDIA_ROOT) / "cortes_processo" / str(self.analysis.id)
        chunks_dir.mkdir(parents=True, exist_ok=True)

        with patch("shutil.rmtree", side_effect=PermissionError("em uso")):
            with self.assertLogs(LOGGER_CLEANUP, level="ERROR") as log:
                _delete_auto_cut_job_files(self.analysis)

        linha = "\n".join(log.output)
        self.assertIn("delete_job_chunks_dir", linha)
        self.assertIn('"target": "directory"', linha)
