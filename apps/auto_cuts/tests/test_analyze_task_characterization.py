"""Characterization tests de `analyze_auto_cuts_task` (refactor.md CT-4 / R-19, D-11).

`analyze_auto_cuts_task` tinha **652 linhas** dentro de `apps/auto_cuts/tasks.py`. Estes
testes foram escritos ANTES da extração (R-19 PR a) e passaram sem alteração de asserção
DEPOIS dela (PR b) — é essa a evidência de que a movimentação preservou comportamento.
O corpo agora mora em `apps/auto_cuts/services/analysis_flow.py`; a task Celery continua
em `tasks.py`, porque o nome dela é contrato de fila.

Só os alvos de `patch()` mudaram de módulo junto com o código, o que é mecânico: o que
cada teste afirma continua idêntico.

O escopo que o CT-4 pede é **só as fronteiras** — entrada, estado final da análise e tasks
enfileiradas. Não o miolo: transcrição, chamada ao LLM e extração de corte dependem de
Whisper, FFmpeg e rede, e testá-los aqui seria reescrever o pipeline no mock. O que estes
testes travam é o contrato observável de fora:

  1. quando a task **desiste em silêncio** (e não pode levantar exceção);
  2. quando ela **para com `status="error"`**, e com qual mensagem exata — a mensagem vai
     para a tela do usuário, então ela é contrato, não detalhe;
  3. quando ela **reagenda a si mesma** em vez de falhar;
  4. quando ela **delega** para outro fluxo.

As mensagens de erro são afirmadas por texto literal de propósito. Se o R-19 mudar uma
delas sem querer, o usuário vê outra coisa na interface e ninguém percebe — é o tipo de
regressão que passa em teste de "status == error".

⚠ FRONTEIRA: `_process_ready_cuts_batch_flow` e `_process_ready_cuts_flow` são fluxos
próprios, de 244 e ~200 linhas. Aqui eles aparecem só como **delegação** — que a task
chama e o que faz com o erro deles. Caracterizá-los por dentro é trabalho do PR seguinte.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutReadyChunk
from apps.auto_cuts.tasks import analyze_auto_cuts_task
from apps.brands.models import Brand, Factory


class AnalyzeTaskFixtureMixin:
    """Monta factory + brand + análise ligados entre si."""

    def build_analysis(self, *, status="pending", paused=False, **campos):
        n = getattr(self, "_seq", 0) + 1
        self._seq = n
        factory = Factory.objects.create(
            name=f"Factory CT4 {n}", processing_paused=paused
        )
        brand = Brand.objects.create(
            name=f"Brand CT4 {n}", slug=f"brand-ct4-{n}", factory=factory
        )
        analysis = AutoCutAnalysis.objects.create(brand=brand, status=status, **campos)
        return factory, brand, analysis


class DesistenciaSilenciosaTests(AnalyzeTaskFixtureMixin, TestCase):
    """Casos em que a task sai sem fazer nada — e sem levantar.

    Todos vêm de entrega duplicada ou fora de ordem do Celery, que com `acks_late` é
    esperado, não excepcional. Levantar aqui encheria o log de erro por comportamento
    normal da fila.
    """

    def test_analise_inexistente_retorna_sem_levantar(self):
        """Task enfileirada para uma análise que o usuário já apagou."""
        analyze_auto_cuts_task.run(999_999)  # não levanta

    def test_status_done_nao_reinicia_a_analise(self):
        """Idempotência: o pipeline inteiro já terminou."""
        _f, _b, analysis = self.build_analysis(status="done", progress=100)

        analyze_auto_cuts_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "done")
        self.assertEqual(analysis.progress, 100)

    def test_status_finalizing_nao_reinicia_a_analise(self):
        """Pós-processamento em andamento: reiniciar duplicaria os cortes."""
        _f, _b, analysis = self.build_analysis(status="finalizing", progress=95)

        analyze_auto_cuts_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "finalizing")
        self.assertEqual(analysis.progress, 95)


class FactoryPausadaTests(AnalyzeTaskFixtureMixin, TestCase):
    """Pausa cooperativa: não interrompe job rodando, só adia o começo de novos."""

    def test_factory_pausada_volta_para_pending_e_reagenda(self):
        _f, _b, analysis = self.build_analysis(
            status="pending", paused=True, progress=50, error="erro anterior"
        )

        with patch.object(analyze_auto_cuts_task, "apply_async") as reagendar:
            analyze_auto_cuts_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "pending")
        self.assertEqual(analysis.progress, 0)
        self.assertEqual(analysis.error, "")
        self.assertEqual(
            analysis.progress_message,
            "Fila de jobs pausada para esta factory. Aguardando retomada...",
        )
        reagendar.assert_called_once_with(args=[analysis.id], countdown=60)

    def test_factory_nao_pausada_nao_reagenda(self):
        """Contraprova: sem pausa, a task segue e falha por falta de vídeo, não por fila."""
        _f, _b, analysis = self.build_analysis(status="pending", paused=False)

        with patch.object(analyze_auto_cuts_task, "apply_async") as reagendar:
            analyze_auto_cuts_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "error")
        reagendar.assert_not_called()


class ParadaComErroTests(AnalyzeTaskFixtureMixin, TestCase):
    """As mensagens são contrato: vão para a tela do usuário."""

    def test_sem_video_para_com_mensagem_de_video_ausente(self):
        _f, _b, analysis = self.build_analysis(status="pending")

        analyze_auto_cuts_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "error")
        self.assertEqual(analysis.error, "Nenhum vídeo encontrado (source ou upload).")

    def test_arquivo_ausente_no_disco_e_distinguido_de_video_ausente(self):
        """Duas falhas diferentes com mensagens diferentes.

        "não anexou vídeo" e "o arquivo sumiu do disco" pedem ações opostas do usuário —
        reenviar contra chamar o suporte. Unificar as mensagens no R-19 seria perda.
        """
        _f, _b, analysis = self.build_analysis(status="pending")
        analysis.file = "auto_cuts/sources/arquivo_que_nao_existe.mp4"
        analysis.save(update_fields=["file"])

        analyze_auto_cuts_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "error")
        self.assertEqual(analysis.error, "Arquivo de vídeo não existe no disco.")


class OverlayOrfaoTests(AnalyzeTaskFixtureMixin, TestCase):
    """Saneamento de FK órfã antes de qualquer save (`_sanitize_long_overlay_fk`)."""

    def test_overlay_apontando_para_asset_apagado_e_desligado(self):
        """Sem isto, o primeiro save da análise estoura `IntegrityError`.

        Acontece quando o `BrandAsset` de overlay é apagado depois de o job ser criado.
        A task limpa a FK e desliga a opção em vez de falhar — o vídeo sai sem overlay,
        que é melhor que não sair.
        """
        _f, _b, analysis = self.build_analysis(status="pending")
        AutoCutAnalysis.objects.filter(id=analysis.id).update(
            long_overlay_enabled=True, long_overlay_asset_id=987_654
        )

        analyze_auto_cuts_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertIsNone(analysis.long_overlay_asset_id)
        self.assertFalse(analysis.long_overlay_enabled)


class DelegacaoReadyCutsTests(AnalyzeTaskFixtureMixin, TestCase):
    """Cortes prontos em lote saem por outro fluxo e a task não segue adiante."""

    def test_com_chunk_delega_para_o_fluxo_de_lote(self):
        _f, _b, analysis = self.build_analysis(status="pending", is_ready_cuts=True)
        AutoCutReadyChunk.objects.create(
            analysis=analysis, order_index=0, file="auto_cuts/ready_chunks/a.mp4"
        )

        with patch("apps.auto_cuts.services.analysis_flow._process_ready_cuts_batch_flow") as fluxo:
            analyze_auto_cuts_task.run(analysis.id)

        fluxo.assert_called_once_with(analysis.id)
        analysis.refresh_from_db()
        # Não caiu no ramo de transcrição: o status não virou "transcribing" nem "error".
        self.assertEqual(analysis.status, "pending")

    def test_erro_no_fluxo_de_lote_vira_status_error_sem_propagar(self):
        """O erro é gravado com `str(e)` cru e a task **não** levanta.

        Levantar faria o Celery reexecutar com `acks_late`, e o lote já pode ter gerado
        arquivos — a segunda tentativa duplicaria. Registrar e sair é a escolha atual.
        """
        _f, _b, analysis = self.build_analysis(status="pending", is_ready_cuts=True)
        AutoCutReadyChunk.objects.create(
            analysis=analysis, order_index=0, file="auto_cuts/ready_chunks/a.mp4"
        )

        with patch(
            "apps.auto_cuts.services.analysis_flow._process_ready_cuts_batch_flow",
            side_effect=RuntimeError("ffmpeg morreu"),
        ):
            analyze_auto_cuts_task.run(analysis.id)  # não levanta

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "error")
        self.assertEqual(analysis.error, "ffmpeg morreu")

    def test_ready_cuts_sem_chunk_nao_delega_e_segue_o_fluxo_normal(self):
        """`is_ready_cuts` sozinho não basta — o lote só existe se houver chunk."""
        _f, _b, analysis = self.build_analysis(status="pending", is_ready_cuts=True)

        with patch("apps.auto_cuts.services.analysis_flow._process_ready_cuts_batch_flow") as fluxo:
            analyze_auto_cuts_task.run(analysis.id)

        fluxo.assert_not_called()
        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "error")
        self.assertEqual(analysis.error, "Nenhum vídeo encontrado (source ou upload).")
