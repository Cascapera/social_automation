"""Characterization tests de `finalizar_auto_cut_task` (refactor.md CT-4 / R-19, D-11).

`finalizar_auto_cut_task` tem **556 linhas** (`apps/auto_cuts/tasks.py:1555`) e recebe
**20 parâmetros**. O R-19 vai extraí-la para `services/finalization_flow.py`; estes testes
são a rede que o plano exige antes disso.

Como no arquivo irmão do `analyze`, o escopo é **só as fronteiras**. O miolo — reframe
vertical, queima de legenda, overlays — é FFmpeg puro e não cabe em teste de unidade. O
que fica travado aqui é o contrato de fora:

  1. **entrada e saída de estado** — a análise entra em `finalizing` e sai em `done`, ou
     fica em `finalizing` com mensagem de recovery quando algo falhou;
  2. **a limpeza dos cortes não selecionados**, que é destrutiva e irreversível;
  3. **o que conta como falha de finalização** e como ela é registrada.

O ponto 3 merece atenção na extração: as falhas **não** levantam exceção nem levam a
análise para `error`. Elas deixam a análise parada em `finalizing` com uma mensagem
específica, porque existe um fluxo de recovery que procura exatamente esse estado
(`apps/auto_cuts/services/recovery.py`). Uma extração que "melhore" isso para `error`
quebra o recovery sem quebrar teste nenhum — a menos que este exista.
"""

from __future__ import annotations

from django.test import TestCase

from apps.auto_cuts.models import (
    AutoCutAnalysis,
    AutoCutCorte,
    AutoCutSuggestion,
)
from apps.auto_cuts.tasks import finalizar_auto_cut_task
from apps.brands.models import Brand, Factory


class FinalizeFixtureMixin:
    """Monta factory + brand + análise, e cortes sob demanda."""

    # A análise entra em "analyzing", e não em "finalizing", de propósito: é a própria
    # task que grava `finalizing`. Começar já nesse estado tornaria as asserções de
    # "ficou em finalizing" verdadeiras mesmo se a task não fizesse nada.
    def build_analysis(self, *, status="analyzing", progress=95, **campos):
        n = getattr(self, "_seq", 0) + 1
        self._seq = n
        factory = Factory.objects.create(name=f"Factory CT4F {n}")
        brand = Brand.objects.create(
            name=f"Brand CT4F {n}", slug=f"brand-ct4f-{n}", factory=factory
        )
        analysis = AutoCutAnalysis.objects.create(
            brand=brand, status=status, progress=progress, **campos
        )
        return factory, brand, analysis

    def build_corte(self, analysis, *, arquivo=None, quer_finalizar=True, finalizado=False):
        sug = AutoCutSuggestion.objects.create(
            analysis=analysis, cut_type="short", start_tc="00:10", end_tc="00:40"
        )
        return AutoCutCorte.objects.create(
            analysis=analysis,
            suggestion=sug,
            format="vertical",
            file=arquivo,
            user_wants_finalize=quer_finalizar,
            is_finalized=finalizado,
        )


class EntradaESaidaDeEstadoTests(FinalizeFixtureMixin, TestCase):
    def test_analise_inexistente_retorna_sem_levantar(self):
        """Entrega duplicada do Celery para análise já apagada."""
        finalizar_auto_cut_task.run(999_999)  # não levanta

    def test_sem_cortes_a_finalizar_a_analise_termina_em_done(self):
        """Nada a fazer é sucesso, não erro.

        Acontece quando o usuário desmarca todos os cortes: a finalização não tem
        trabalho, e a análise precisa sair de `finalizing` mesmo assim — senão fica presa
        para sempre num estado que o recovery vai tentar destravar em vão.
        """
        _f, _b, analysis = self.build_analysis()

        finalizar_auto_cut_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "done")
        self.assertEqual(analysis.progress, 100)
        self.assertEqual(analysis.progress_message, "Concluído")
        self.assertEqual(analysis.error, "")

    def test_progresso_nunca_retrocede_ao_entrar_em_finalizing(self):
        """O progresso de entrada é `min(99, max(atual, 95))`.

        Uma análise que chegou aqui com 98 não pode voltar para 95 — a barra andaria para
        trás na tela. E o teto de 99 existe para que só o fim marque 100.
        """
        _f, _b, analysis = self.build_analysis(progress=98)
        self.build_corte(analysis, arquivo="auto_cuts/cortes/inexistente.mp4")

        finalizar_auto_cut_task.run(analysis.id)

        # O corte falha e a análise fica em finalizing — o que interessa aqui é que o
        # progresso passou por 98, não por 95.
        analysis.refresh_from_db()
        self.assertGreaterEqual(analysis.progress, 98)
        self.assertLessEqual(analysis.progress, 99)

    def test_overlay_apontando_para_asset_apagado_e_desligado(self):
        """Mesmo saneamento do `analyze`: FK órfã derrubaria o primeiro save."""
        _f, _b, analysis = self.build_analysis()
        AutoCutAnalysis.objects.filter(id=analysis.id).update(
            long_overlay_enabled=True, long_overlay_asset_id=987_654
        )

        finalizar_auto_cut_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertIsNone(analysis.long_overlay_asset_id)
        self.assertFalse(analysis.long_overlay_enabled)


class LimpezaDeCortesNaoSelecionadosTests(FinalizeFixtureMixin, TestCase):
    """A parte destrutiva e irreversível da task."""

    def test_corte_nao_selecionado_e_apagado_do_banco(self):
        _f, _b, analysis = self.build_analysis()
        descartado = self.build_corte(analysis, quer_finalizar=False)

        finalizar_auto_cut_task.run(analysis.id)

        self.assertFalse(AutoCutCorte.objects.filter(id=descartado.id).exists())

    def test_corte_selecionado_sobrevive(self):
        """Contraprova: a exclusão é dirigida por `user_wants_finalize`, e só por ele."""
        _f, _b, analysis = self.build_analysis()
        mantido = self.build_corte(
            analysis, arquivo="auto_cuts/cortes/inexistente.mp4", quer_finalizar=True
        )

        finalizar_auto_cut_task.run(analysis.id)

        self.assertTrue(AutoCutCorte.objects.filter(id=mantido.id).exists())


class FalhaDeFinalizacaoTests(FinalizeFixtureMixin, TestCase):
    """Falha de corte não vira `error` — vira estado que o recovery reconhece."""

    def test_corte_sem_arquivo_deixa_a_analise_pendente_de_recovery(self):
        _f, _b, analysis = self.build_analysis()
        self.build_corte(analysis, arquivo=None)

        finalizar_auto_cut_task.run(analysis.id)  # não levanta

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "finalizing")
        self.assertEqual(analysis.progress_message, "Finalização pendente de recovery.")
        self.assertIn("1 corte(s) com falha", analysis.error)

    def test_arquivo_ausente_no_disco_tambem_e_falha_de_finalizacao(self):
        _f, _b, analysis = self.build_analysis()
        self.build_corte(analysis, arquivo="auto_cuts/cortes/sumiu.mp4")

        finalizar_auto_cut_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, "finalizing")
        self.assertEqual(analysis.progress_message, "Finalização pendente de recovery.")

    def test_corte_que_falha_perde_a_marca_de_finalizado(self):
        """Corte marcado como pronto que falha na re-finalização volta a "não finalizado".

        Sem isso, uma segunda passada o consideraria pronto e ele ficaria para sempre
        marcado como finalizado sem nunca ter sido processado.
        """
        _f, _b, analysis = self.build_analysis()
        corte = self.build_corte(analysis, arquivo=None, finalizado=True)

        finalizar_auto_cut_task.run(analysis.id)

        corte.refresh_from_db()
        self.assertFalse(corte.is_finalized)

    def test_contagem_de_falhas_aparece_na_mensagem_de_erro(self):
        """A mensagem carrega o número de cortes e de sincronizações que falharam.

        É o que a tela mostra e o que orienta quem for investigar — dois cortes com falha
        e zero de inventário pede ação diferente do inverso.
        """
        _f, _b, analysis = self.build_analysis()
        self.build_corte(analysis, arquivo=None)
        self.build_corte(analysis, arquivo=None)

        finalizar_auto_cut_task.run(analysis.id)

        analysis.refresh_from_db()
        self.assertIn("2 corte(s) com falha", analysis.error)
        self.assertIn("0 sincronização(ões) com falha", analysis.error)
