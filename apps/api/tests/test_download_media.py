"""Download de mídia para postagem manual (refactor.md R-20 PR 2 / D-10).

O endpoint monta um ZIP com o vídeo, a thumbnail e um txt de título/descrição. As duas
inclusões de arquivo eram `try/except: pass`, e havia ainda um terceiro caminho silencioso:
`if fp.exists()` — quando o banco diz que a mídia existe e o disco discorda, o arquivo era
simplesmente pulado.

O resultado é o pior tipo de falha: **200 com um pacote incompleto**. O usuário baixa,
abre, e descobre na hora de postar. Ou pior, não descobre.

⚠ Este arquivo cobre uma **mudança de comportamento observável**, e é por isso que ela foi
para um PR próprio:

  - pacote parcial continua sendo entregue (200), mas agora leva um
    `ATENCAO_arquivos_faltando.txt` dentro e o cabeçalho `X-Missing-Media`;
  - pacote em que **nada** de mídia entrou passa a responder **404** em vez de 200 com um
    ZIP só de texto. Um ZIP sem vídeo nem thumbnail não serve para postar — é melhor o
    usuário saber agora.
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.brands.models import Brand, Factory
from apps.jobs.models import VideoInventoryItem

User = get_user_model()
LOGGER_POSTING = "apps.api.views.posting"


class DownloadMediaTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="dl-user", password="securepass1")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        factory = Factory.objects.create(name="Factory DL")
        self.brand = Brand.objects.create(name="Brand DL", slug="brand-dl", factory=factory)
        analysis = AutoCutAnalysis.objects.create(brand=self.brand, status="done")
        sug = AutoCutSuggestion.objects.create(
            analysis=analysis, cut_type="short", start_tc="00:10", end_tc="00:40"
        )
        self.corte = AutoCutCorte.objects.create(
            analysis=analysis, suggestion=sug, format="vertical"
        )
        self.item = VideoInventoryItem.objects.create(
            factory=factory,
            brand=self.brand,
            auto_cut_corte=self.corte,
            video_type="SHORT",
            title="Meu vídeo",
            status="AVAILABLE",
        )

    def anexar_video(self, conteudo=b"video-de-verdade"):
        self.corte.file.save("dl_video.mp4", ContentFile(conteudo), save=True)

    def anexar_thumb(self, conteudo=b"thumb-de-verdade"):
        self.corte.thumbnail.save("dl_thumb.jpg", ContentFile(conteudo), save=True)

    def baixar(self):
        return self.client.get(f"/api/video-inventory/{self.item.id}/download-media/")

    def nomes_no_zip(self, res):
        conteudo = b"".join(res.streaming_content)
        return zipfile.ZipFile(io.BytesIO(conteudo)).namelist()

    def test_pacote_completo_nao_avisa_nada(self):
        """Contraprova: sem falha, nada muda em relação ao comportamento antigo."""
        self.anexar_video()
        self.anexar_thumb()

        res = self.baixar()

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotIn("X-Missing-Media", res)
        nomes = self.nomes_no_zip(res)
        self.assertIn("Meu vídeo.mp4", nomes)
        self.assertIn("Meu vídeo_thumb.jpg", nomes)
        self.assertIn("Meu vídeo_descricao.txt", nomes)
        self.assertNotIn("ATENCAO_arquivos_faltando.txt", nomes)

    def test_sem_midia_nenhuma_no_banco_continua_404(self):
        """Comportamento antigo preservado: o item nunca teve arquivo."""
        res = self.baixar()

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["error"], "Nenhuma mídia disponível para download.")

    def test_thumb_sumida_do_disco_avisa_e_entrega_o_video(self):
        """Pacote parcial ainda serve: o vídeo é o que importa para postar."""
        self.anexar_video()
        self.anexar_thumb()
        self.corte.thumbnail.storage.delete(self.corte.thumbnail.name)

        with self.assertLogs(LOGGER_POSTING, level="ERROR") as log:
            res = self.baixar()

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["X-Missing-Media"], "thumbnail")
        nomes = self.nomes_no_zip(res)
        self.assertIn("Meu vídeo.mp4", nomes)
        self.assertIn("ATENCAO_arquivos_faltando.txt", nomes)
        self.assertIn("download_media_missing", "\n".join(log.output))

    def test_o_aviso_dentro_do_zip_diz_o_que_faltou_e_o_numero_do_item(self):
        """Quem abre o ZIP precisa saber o que pedir ao suporte."""
        self.anexar_video()
        self.anexar_thumb()
        self.corte.thumbnail.storage.delete(self.corte.thumbnail.name)

        with self.assertLogs(LOGGER_POSTING, level="ERROR"):
            res = self.baixar()

        conteudo = b"".join(res.streaming_content)
        aviso = zipfile.ZipFile(io.BytesIO(conteudo)).read("ATENCAO_arquivos_faltando.txt").decode()
        self.assertIn("thumbnail", aviso)
        self.assertIn(str(self.item.id), aviso)
        self.assertNotIn("vídeo,", aviso)

    def test_video_sumido_do_disco_avisa_e_entrega_a_thumb(self):
        self.anexar_video()
        self.anexar_thumb()
        self.corte.file.storage.delete(self.corte.file.name)

        with self.assertLogs(LOGGER_POSTING, level="ERROR"):
            res = self.baixar()

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["X-Missing-Media"], "vídeo")
        self.assertIn("Meu vídeo_thumb.jpg", self.nomes_no_zip(res))

    def test_tudo_sumido_do_disco_vira_404(self):
        """⚠ Mudança de comportamento: antes era 200 com um ZIP só de texto.

        O banco dizia que a mídia existia; o disco não tinha nada. Entregar um pacote sem
        vídeo nem thumbnail é entregar algo que não serve para postar.
        """
        self.anexar_video()
        self.anexar_thumb()
        self.corte.file.storage.delete(self.corte.file.name)
        self.corte.thumbnail.storage.delete(self.corte.thumbnail.name)

        with self.assertLogs(LOGGER_POSTING, level="ERROR"):
            res = self.baixar()

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("não está mais disponível", res.data["error"])

    def test_falha_de_leitura_tambem_conta_como_faltando(self):
        """Arquivo no lugar, mas ilegível: permissão, disco com erro, mount caído."""
        self.anexar_video()

        with patch("zipfile.ZipFile.write", side_effect=OSError("erro de leitura")):
            with self.assertLogs(LOGGER_POSTING, level="ERROR") as log:
                res = self.baixar()

        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("download_media_failed", "\n".join(log.output))
        self.assertIn("erro de leitura", "\n".join(log.output))

    def test_item_ja_postado_continua_recusado(self):
        """Contraprova de que a guarda anterior não foi afetada."""
        self.item.status = "POSTED"
        self.item.save(update_fields=["status"])

        res = self.baixar()

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
