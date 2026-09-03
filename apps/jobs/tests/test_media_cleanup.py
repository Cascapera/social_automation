"""Remoção de mídia com falha visível (refactor.md R-20 / D-10).

O D-10 contou 52 pontos que engolem exceção sem log, métrica ou sinal. Os primeiros que o
R-20 trata são os de remoção de mídia, porque a falha ali tem custo cumulativo: o registro
sai do banco, o arquivo fica no disco, e não há erro, contador ou log dizendo isso. O
sintoma aparece meses depois, como "o disco encheu".

O que estes testes travam:

  1. o fluxo **continua tolerante** — falhar ao apagar não levanta e não interrompe quem
     estava no meio de uma remoção;
  2. a falha **aparece no log**, com o `operation` de quem pediu — é isso que permite ler o
     log e saber qual fluxo está deixando arquivo para trás;
  3. o **contador não mente**: o que não foi apagado não é contado como apagado.

O ponto 3 é o que distingue este item de "só adicionar log". A resposta de
`remove-awaiting` diz ao usuário quantos arquivos saíram; contar um que ficou seria pior
que não contar nada.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.brands.models import Brand, Factory
from apps.jobs.models import VideoInventoryItem
from apps.jobs.services.inventory_actions import remove_awaiting_item
from apps.jobs.services.media_cleanup import delete_file_field, unlink_path

LOGGER_CLEANUP = "apps.jobs.services.media_cleanup"


class CampoFalso:
    """`FileField` de mentira: registra a chamada ou levanta, conforme o teste pedir."""

    def __init__(self, *, erro=None, name="auto_cuts/cortes/x.mp4"):
        self.erro = erro
        self.name = name
        self.chamou = False

    def __bool__(self):
        return True

    def delete(self, save=True):
        self.chamou = True
        if self.erro:
            raise self.erro


class DeleteFileFieldTests(SimpleTestCase):
    def test_apaga_e_devolve_true(self):
        campo = CampoFalso()

        self.assertTrue(delete_file_field(campo, operation="teste"))
        self.assertTrue(campo.chamou)

    def test_campo_vazio_nao_e_falha_e_nao_loga(self):
        """Não ter arquivo é normal — só o que falhou ao apagar vira evento."""
        with patch("apps.jobs.services.media_cleanup.log_event") as evento:
            self.assertFalse(delete_file_field(None, operation="teste"))
        evento.assert_not_called()

    def test_falha_nao_levanta_e_devolve_false(self):
        campo = CampoFalso(erro=OSError("disco em modo leitura"))

        with self.assertLogs(LOGGER_CLEANUP, level="ERROR"):
            self.assertFalse(delete_file_field(campo, operation="teste"))

    def test_falha_registra_quem_pediu_e_o_arquivo(self):
        """`operation` é o que permite achar o fluxo culpado no log."""
        campo = CampoFalso(erro=OSError("disco em modo leitura"), name="auto_cuts/cortes/a.mp4")

        with self.assertLogs(LOGGER_CLEANUP, level="ERROR") as log:
            delete_file_field(campo, operation="remove_awaiting", corte_id=42)

        linha = "\n".join(log.output)
        self.assertIn("media_delete_failed", linha)
        self.assertIn("remove_awaiting", linha)
        self.assertIn("auto_cuts/cortes/a.mp4", linha)
        self.assertIn("disco em modo leitura", linha)
        self.assertIn("42", linha)


class UnlinkPathTests(SimpleTestCase):
    def test_caminho_inexistente_nao_e_falha(self):
        """O arquivo já ter sumido é o caso bom: alguém apagou antes."""
        with patch("apps.jobs.services.media_cleanup.log_event") as evento:
            self.assertFalse(
                unlink_path(Path("/caminho/que/nao/existe.mp4"), operation="teste")
            )
        evento.assert_not_called()

    def test_caminho_none_nao_e_falha(self):
        self.assertFalse(unlink_path(None, operation="teste"))

    def test_falha_ao_apagar_vira_evento(self):
        caminho = Path("/qualquer/arquivo.mp4")
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "unlink", side_effect=PermissionError("arquivo em uso")
        ):
            with self.assertLogs(LOGGER_CLEANUP, level="ERROR") as log:
                self.assertFalse(unlink_path(caminho, operation="finalize_discard"))

        linha = "\n".join(log.output)
        self.assertIn("media_delete_failed", linha)
        self.assertIn("finalize_discard", linha)
        self.assertIn("arquivo em uso", linha)


class RemoveAwaitingComFalhaDeDiscoTests(TestCase):
    """A falha atravessando o fluxo inteiro, até a resposta que o usuário lê."""

    def setUp(self):
        super().setUp()
        factory = Factory.objects.create(name="Factory R20")
        self.brand = Brand.objects.create(name="Brand R20", slug="brand-r20", factory=factory)
        analysis = AutoCutAnalysis.objects.create(brand=self.brand, status="done")
        sug = AutoCutSuggestion.objects.create(
            analysis=analysis, cut_type="short", start_tc="00:10", end_tc="00:40"
        )
        self.corte = AutoCutCorte.objects.create(
            analysis=analysis,
            suggestion=sug,
            format="vertical",
            file="auto_cuts/cortes/r20.mp4",
        )
        self.item = VideoInventoryItem.objects.create(
            factory=factory,
            brand=self.brand,
            auto_cut_corte=self.corte,
            video_type="SHORT",
            status="AVAILABLE",
        )

    def test_falha_ao_apagar_arquivo_nao_impede_a_remocao(self):
        """O item sai do banco mesmo assim — a remoção não pode ficar pela metade."""
        with patch(
            "django.db.models.fields.files.FieldFile.delete",
            side_effect=OSError("disco cheio"),
        ):
            with self.assertLogs(LOGGER_CLEANUP, level="ERROR"):
                resposta = remove_awaiting_item(self.item)

        self.assertTrue(resposta["ok"])
        self.assertFalse(VideoInventoryItem.objects.filter(id=self.item.id).exists())

    def test_o_contador_nao_conta_o_que_ficou_no_disco(self):
        """Antes do R-20 o número já era honesto; agora existe teste garantindo que continue.

        É o que separa este item de "só adicionar log": a resposta diz ao usuário quantos
        arquivos saíram, e contar um que ficou seria pior que não contar nada.
        """
        with patch(
            "django.db.models.fields.files.FieldFile.delete",
            side_effect=OSError("disco cheio"),
        ):
            with self.assertLogs(LOGGER_CLEANUP, level="ERROR"):
                resposta = remove_awaiting_item(self.item)

        self.assertEqual(resposta["deleted_media_files"], 0)

    def test_o_log_identifica_o_corte_e_o_item(self):
        """Sem o id, o evento diz que algo falhou mas não o que ficou para trás."""
        # Guardado antes: o Django zera o `pk` do objeto depois do `delete()`.
        item_id = self.item.id
        with patch(
            "django.db.models.fields.files.FieldFile.delete",
            side_effect=OSError("disco cheio"),
        ):
            with self.assertLogs(LOGGER_CLEANUP, level="ERROR") as log:
                remove_awaiting_item(self.item)

        linha = "\n".join(log.output)
        self.assertIn("remove_awaiting", linha)
        self.assertIn(f'"corte_id": {self.corte.id}', linha)
        self.assertIn(f'"inventory_item_id": {item_id}', linha)
