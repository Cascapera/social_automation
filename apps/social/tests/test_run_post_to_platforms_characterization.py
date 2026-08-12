"""Characterization tests de `_run_post_to_platforms` (refactor.md R-04 / D-01).

`apps/social/tasks.py:_run_post_to_platforms` tem **1.189 linhas** e é o coração da
publicação: resolve a origem do vídeo (job ou corte), valida o slot da factory, reivindica
o post contra outros workers, publica via Upload-Post ou API nativa do YouTube e no fim
grava estado em 4 modelos. R-09 a R-13 vão fatiá-la — estes testes são a rede.

Estes testes NÃO julgam o comportamento: eles **fixam o comportamento atual**, inclusive o
que parece bug, para que a fatiagem seja provadamente sem efeito colateral. Onde o
comportamento de hoje é suspeito, o comentário diz isso — mas a asserção continua afirmando
o que o código faz hoje.

O que está coberto aqui:

  1. As 9 saídas antecipadas, na ordem em que aparecem no código:
     - `tasks.py:2479`  ScheduledPost inexistente
     - `tasks.py:2484`  status != PENDING
     - `tasks.py:2497`  job sem marca
     - `tasks.py:2503`  job sem vídeo final
     - `tasks.py:2512`  corte sem marca
     - `tasks.py:2517`  corte sem arquivo
     - `tasks.py:2523`  post sem origem (nem job nem corte)
     - `tasks.py:2556`  corrida de claim perdida para outro worker
     - `tasks.py:2596`  factory com agendamento pausado
  2. Slot da factory expirado — antes de começar e durante a pausa da factory.
  3. Caminho feliz até `status=DONE`, com o estado final completo.

⚠ GAP CONHECIDO (registrado no refactor.md, item R-04): o ramo de
`YOUTUBE_CHECK_CLIENT_ENABLED` (`apps/social/tasks.py:65`) **não é coberto** por estes
testes. A flag é lida no import do módulo, então não dá para exercitá-la sem
`override_settings` funcional — o que depende do **R-17**. Ao fechar o R-17, voltar aqui e
adicionar o caso.

⚠ FRONTEIRA: a reconciliação do Upload-Post (`_try_pending_upload_post_reconciliation`) já
tem cobertura própria e mais profunda em `test_upload_post_reconciliation.py`. Aqui ela
aparece só como guarda de saída — não duplicar aqueles casos.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.brands.models import Brand, BrandSocialAccount, Factory
from apps.jobs.models import (
    DailyPostingPlan,
    DailyPostingPlanItem,
    FactoryPostingAttemptLog,
    FactoryPostingSchedule,
    Job,
    RenderOutput,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.social.tasks import _run_post_to_platforms

User = get_user_model()

UPLOAD_POST_PATCH_TARGET = "apps.social.publishers.upload_post.publish_to_upload_post"
NATIVE_PUBLISHER_PATCH_TARGET = "apps.social.publishers.get_publisher"


class RunPostToPlatformsFixtureMixin:
    """Monta o mínimo que `_run_post_to_platforms` exige para chegar em cada guarda."""

    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, True)

        self.factory = Factory.objects.create(name="Factory Caracterizacao R04")
        self.brand = Brand.objects.create(
            name="Brand Caracterizacao R04",
            slug="brand-caracterizacao-r04",
            factory=self.factory,
            upload_post_tiktok_enabled=False,
            upload_post_x_enabled=False,
            upload_post_instagram_enabled=False,
            upload_post_youtube_enabled=True,
        )
        self.user = User.objects.create_user(username="r04-user", password="securepass1")
        self.account = BrandSocialAccount.objects.create(
            brand=self.brand,
            platform="YTB",
            channel_id="channel-r04",
            account_name="Canal R04",
        )
        self.job = self._build_job(brand=self.brand, with_output=True)

    def _build_job(self, *, brand, with_output: bool) -> Job:
        job = Job.objects.create(user=self.user, brand=brand, name="Job R04")
        if with_output:
            RenderOutput.objects.create(
                job=job,
                file=SimpleUploadedFile("video.mp4", b"x" * 2048, content_type="video/mp4"),
            )
        return job

    def _post(self, **kwargs) -> ScheduledPost:
        """Post de job, plataforma YTB (long-form), pronto para publicar."""
        defaults = dict(
            job=self.job,
            social_account=self.account,
            platforms=["YTB"],
            scheduled_at=timezone.now() - timedelta(seconds=1),
            title="Título R04",
            status="PENDING",
        )
        defaults.update(kwargs)
        return ScheduledPost.objects.create(**defaults)

    def _build_corte(self, *, brand, with_file: bool) -> AutoCutCorte:
        analysis = AutoCutAnalysis.objects.create(brand=brand, name="Analise R04", status="done")
        suggestion = AutoCutSuggestion.objects.create(
            analysis=analysis,
            cut_type="short",
            start_tc="00:00",
            end_tc="00:30",
            title="Corte R04",
            source_asset_id="source-r04",
        )
        corte = AutoCutCorte.objects.create(
            analysis=analysis,
            suggestion=suggestion,
            format="vertical",
            needs_subtitle=False,
            user_wants_finalize=True,
            is_finalized=True,
        )
        if with_file:
            corte.file.save(
                "corte_r04.mp4",
                SimpleUploadedFile("corte_r04.mp4", b"cut-video", content_type="video/mp4"),
                save=True,
            )
        return corte

    def _attach_factory_slot(
        self,
        post: ScheduledPost,
        *,
        slot_at,
        with_daily_plan_item: bool = True,
    ) -> FactoryPostingSchedule:
        """Liga o post a um slot da factory.

        `daily_plan_item` é o que faz o slot ter deadline: sem ele,
        `_factory_slot_deadline` devolve None e a publicação é tratada como avulsa.
        """
        item = VideoInventoryItem.objects.create(
            factory=self.factory,
            brand=self.brand,
            video_type="LONG",
            status="SCHEDULED",
            title="Item R04",
            scheduled_for=slot_at,
        )
        plan_item = None
        if with_daily_plan_item:
            plan = DailyPostingPlan.objects.create(
                brand=self.brand,
                plan_date=slot_at.date(),
                timezone=self.factory.timezone or "America/Sao_Paulo",
                status=DailyPostingPlan.Status.GENERATED,
                planned_posts_count=1,
            )
            plan_item = DailyPostingPlanItem.objects.create(
                plan=plan,
                order_index=0,
                video_type="LONG",
                scheduled_at=slot_at,
                status=DailyPostingPlanItem.Status.CONSUMED,
                inventory_item=item,
                scheduled_post=post,
            )
        return FactoryPostingSchedule.objects.create(
            factory=self.factory,
            brand=self.brand,
            inventory_item=item,
            video_type="LONG",
            scheduled_at=slot_at,
            status="PLANNED",
            scheduled_post=post,
            daily_plan_item=plan_item,
        )

    def _run(self, post_id: int, *, upload_post_result=None, upload_post_side_effect=None):
        """Executa a função com o provedor externo mockado.

        Nenhum teste deste arquivo pode tocar a rede: `publish_to_upload_post` e o
        publisher nativo do YouTube são sempre substituídos.
        """
        with patch(UPLOAD_POST_PATCH_TARGET) as mock_up, patch(
            NATIVE_PUBLISHER_PATCH_TARGET
        ) as mock_native:
            if upload_post_side_effect is not None:
                mock_up.side_effect = upload_post_side_effect
            else:
                mock_up.return_value = upload_post_result or {
                    "success": True,
                    "request_id": "req-r04",
                    "provider_request_id": "req-r04",
                    "request_id_source": "provider",
                    "data": {},
                }
            result = _run_post_to_platforms(post_id)
        return result, mock_up, mock_native


class RunPostToPlatformsEarlyExitTests(RunPostToPlatformsFixtureMixin, TestCase):
    """As 9 saídas antecipadas, na ordem do código."""

    def test_post_inexistente_devolve_erro_sem_tocar_no_banco(self):
        """tasks.py:2479 — id que não existe sai antes de qualquer efeito."""
        result, mock_up, mock_native = self._run(999_999)

        self.assertEqual(result, {"error": "ScheduledPost não encontrado"})
        mock_up.assert_not_called()
        mock_native.assert_not_called()

    def test_status_diferente_de_pending_e_ignorado(self):
        """tasks.py:2484 — só PENDING publica; o resto sai como 'skipped'.

        Vale para qualquer status não-PENDING (DONE, FAILED, POSTING). O teste usa DONE
        porque é o caso real: reenfileiramento duplicado de um post já publicado.
        """
        post = self._post(status="DONE")

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"skipped": "status não é PENDING"})
        self.assertEqual(post.status, "DONE")
        mock_up.assert_not_called()

    def test_job_sem_marca_marca_failed(self):
        """tasks.py:2497 — job sem brand encerra o post como FAILED."""
        job_sem_marca = self._build_job(brand=None, with_output=True)
        post = self._post(job=job_sem_marca)

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"error": "Job sem marca"})
        self.assertEqual(post.status, "FAILED")
        self.assertEqual(post.error, "Job sem marca")
        mock_up.assert_not_called()

    def test_job_com_render_output_vazio_marca_failed(self):
        """tasks.py:2503 — RenderOutput existe mas sem arquivo: encerra como FAILED.

        Este é o único caminho em que a guarda "Job sem vídeo final" realmente dispara —
        ver o teste seguinte para o caso em que ela é inalcançável.
        """
        job = self._build_job(brand=self.brand, with_output=False)
        RenderOutput.objects.create(job=job)
        post = self._post(job=job)

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"error": "Job sem vídeo final"})
        self.assertEqual(post.status, "FAILED")
        self.assertEqual(post.error, "Job sem vídeo final")
        mock_up.assert_not_called()

    def test_job_sem_render_output_levanta_excecao_em_vez_de_falhar(self):
        """⚠ BUG CARACTERIZADO — a guarda de `tasks.py:2503` é inalcançável sem RenderOutput.

        `output = post.job.output` (tasks.py:2498) acessa um OneToOne reverso. Quando o job
        não tem nenhum RenderOutput, o próprio acesso levanta
        `RelatedObjectDoesNotExist` — a linha seguinte, que trataria o caso como FAILED,
        nunca executa.

        Consequência em produção: o post fica preso em PENDING (não vira FAILED) e a task
        Celery estoura, consumindo as 3 tentativas de retry sem nunca registrar o motivo
        no post. Só aparece no log do worker.

        Este teste afirma o comportamento de HOJE. A correção vai em PR próprio com
        prefixo `fix()` — ver R-22 no refactor.md. Quando ela entrar, este teste inverte:
        vira `{"error": "Job sem vídeo final"}` e o post em FAILED.
        """
        job_sem_output = self._build_job(brand=self.brand, with_output=False)
        post = self._post(job=job_sem_output)

        with self.assertRaises(RenderOutput.DoesNotExist):
            self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(post.error, "")

    def test_corte_sem_marca_marca_failed(self):
        """tasks.py:2512 — a marca do corte vem da análise; sem ela, FAILED.

        Note que a mensagem de erro persistida diz "AutoCut sem marca" enquanto o retorno
        da função diz o mesmo — diferente das guardas de slot, onde os dois divergem.
        """
        corte = self._build_corte(brand=None, with_file=True)
        post = self._post(job=None, auto_cut_corte=corte, platforms=["YT"])

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"error": "AutoCut sem marca"})
        self.assertEqual(post.status, "FAILED")
        self.assertEqual(post.error, "AutoCut sem marca")
        mock_up.assert_not_called()

    def test_corte_sem_arquivo_marca_failed(self):
        """tasks.py:2517 — corte finalizado mas sem arquivo em disco encerra como FAILED."""
        corte = self._build_corte(brand=self.brand, with_file=False)
        post = self._post(job=None, auto_cut_corte=corte, platforms=["YT"])

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"error": "AutoCut sem vídeo finalizado"})
        self.assertEqual(post.status, "FAILED")
        self.assertEqual(post.error, "AutoCut sem vídeo finalizado")
        mock_up.assert_not_called()

    def test_post_sem_origem_marca_failed(self):
        """tasks.py:2523 — sem job e sem corte não há vídeo para publicar.

        ⚠ O retorno ("ScheduledPost sem origem") e o erro persistido
        ("ScheduledPost sem origem (job/corte)") são textos DIFERENTES. É assim hoje;
        quem unificar as mensagens no R-09 precisa decidir qual fica.
        """
        post = self._post(job=None, auto_cut_corte=None)

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"error": "ScheduledPost sem origem"})
        self.assertEqual(post.status, "FAILED")
        self.assertEqual(post.error, "ScheduledPost sem origem (job/corte)")
        mock_up.assert_not_called()

    def test_claim_perdido_para_outro_worker_sai_como_skipped(self):
        """tasks.py:2556 — a reivindicação é um UPDATE condicional em PENDING.

        Se outro worker reivindicou o post entre o `.get()` e o `.update()`, o UPDATE
        afeta 0 linhas e esta execução desiste. É a única proteção contra publicação
        dupla nesta etapa — o R-11 não pode removê-la.

        A corrida é simulada mexendo no banco de dentro da guarda anterior, que roda
        exatamente entre a leitura e a reivindicação.
        """
        post = self._post()

        def _outro_worker_reivindica(post_arg, brand_arg, **kwargs):
            ScheduledPost.objects.filter(id=post_arg.id).update(status="POSTING")
            return None

        with patch(
            "apps.social.tasks._try_pending_upload_post_reconciliation",
            side_effect=_outro_worker_reivindica,
        ):
            result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"skipped": "status não é PENDING"})
        self.assertEqual(post.status, "POSTING")
        mock_up.assert_not_called()

    def test_factory_pausada_reagenda_para_cinco_minutos(self):
        """tasks.py:2596 — pausa segura o post, não o mata.

        A pausa da factory não interrompe a geração de conteúdo: ela devolve o post para
        PENDING com `scheduled_at` cinco minutos à frente, para ser reavaliado. O post
        chega a passar por POSTING antes disso (a reivindicação já aconteceu), mas volta
        para PENDING no mesmo ciclo.
        """
        self.factory.scheduling_paused = True
        self.factory.save(update_fields=["scheduling_paused"])
        post = self._post()
        antes = timezone.now()

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"skipped": "factory scheduling paused"})
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(post.error, "Agendamento da factory pausado. Aguardando retomada.")
        self.assertGreater(post.scheduled_at, antes + timedelta(minutes=4))
        self.assertLess(post.scheduled_at, antes + timedelta(minutes=6))
        mock_up.assert_not_called()

    def test_pausa_da_factory_e_verificada_apenas_quando_ha_factory(self):
        """A guarda de pausa depende de `brand.factory_id`; marca solta a ignora.

        Fixa que uma marca sem factory publica normalmente mesmo com outra factory pausada
        no sistema — o R-12 não pode trocar isso por uma checagem global.
        """
        self.factory.scheduling_paused = True
        self.factory.save(update_fields=["scheduling_paused"])
        brand_sem_factory = Brand.objects.create(
            name="Brand sem factory R04",
            slug="brand-sem-factory-r04",
            factory=None,
            upload_post_youtube_enabled=True,
        )
        job = self._build_job(brand=brand_sem_factory, with_output=True)
        post = self._post(job=job, social_account=None)

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertNotEqual(result.get("skipped"), "factory scheduling paused")
        self.assertEqual(post.status, "DONE")
        mock_up.assert_called_once()


class RunPostToPlatformsExpiredSlotTests(RunPostToPlatformsFixtureMixin, TestCase):
    """Slot da factory expirado — a janela de postagem é dura, não elástica."""

    def test_slot_expirado_antes_de_comecar_falha_sem_publicar(self):
        """O deadline é checado antes da reivindicação: nem chega a POSTING."""
        post = self._post()
        slot_at = timezone.now() - timedelta(minutes=30)
        schedule = self._attach_factory_slot(post, slot_at=slot_at)

        result, mock_up, mock_native = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(post.status, "FAILED")
        self.assertIs(post.external_ids.get("slot_expired"), True)
        self.assertEqual(post.external_ids.get("slot_deadline_at"), slot_at.isoformat())
        self.assertIn("Janela de postagem expirada", post.error)
        self.assertIn("já passou antes de iniciar uma nova tentativa", post.error)
        self.assertEqual(result.get("status"), "FAILED")
        mock_up.assert_not_called()
        mock_native.assert_not_called()

        log = FactoryPostingAttemptLog.objects.filter(posting_schedule=schedule).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.result, "ERROR")
        self.assertEqual(log.attempt_number, 1)

    def test_slot_expirado_usa_retry_count_como_numero_da_tentativa(self):
        """`attempt_number` é `retry_count + 1` — a primeira tentativa é a de número 1."""
        post = self._post(retry_count=3)
        schedule = self._attach_factory_slot(post, slot_at=timezone.now() - timedelta(minutes=30))

        self._run(post.id)

        log = FactoryPostingAttemptLog.objects.filter(posting_schedule=schedule).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.attempt_number, 4)

    def test_slot_expirado_limpa_estado_de_reconciliacao_pendente(self):
        """Ao expirar, o rastro da reconciliação do Upload-Post é apagado.

        Sem isso o post ficaria FAILED mas ainda elegível para a varredura de
        reconciliação, que poderia ressuscitá-lo depois do prazo.
        """
        post = self._post(
            external_ids={
                "upload_post_reconciliation_state": "pending",
                "upload_post_no_provider_id_check_count": 2,
                "upload_post_resend_count": 1,
                "upload_post_request_id": "req-preservado",
            }
        )
        self._attach_factory_slot(post, slot_at=timezone.now() - timedelta(minutes=30))

        self._run(post.id)

        post.refresh_from_db()
        self.assertNotIn("upload_post_reconciliation_state", post.external_ids)
        self.assertNotIn("upload_post_no_provider_id_check_count", post.external_ids)
        self.assertNotIn("upload_post_resend_count", post.external_ids)
        # O request_id do provedor é preservado: serve de rastro para auditoria.
        self.assertEqual(post.external_ids.get("upload_post_request_id"), "req-preservado")

    def test_publicacao_avulsa_sem_daily_plan_item_nao_tem_deadline(self):
        """Sem `daily_plan_item`, o slot não expira — republicação manual é sempre válida.

        Este é o caso do `run_scheduled_posts_now` e do botão de retry no painel: o slot
        já passou há muito, mas a publicação precisa acontecer mesmo assim.
        """
        post = self._post()
        self._attach_factory_slot(
            post,
            slot_at=timezone.now() - timedelta(days=2),
            with_daily_plan_item=False,
        )

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(post.status, "DONE")
        self.assertNotIn("slot_expired", post.external_ids)
        self.assertEqual(result.get("status"), "DONE")
        mock_up.assert_called_once()

    def test_factory_pausada_com_slot_que_expira_antes_da_proxima_checagem_falha(self):
        """A pausa não empurra o slot: se ele morre nos 5 min de espera, morre agora.

        O slot ainda é válido no início da execução, então a primeira checagem passa. Mas
        a segunda usa `now + 5min` — o horário em que o post seria reavaliado — e nesse
        instante o slot já expirou. O post falha em vez de ser reagendado para um horário
        que já não serve.
        """
        self.factory.scheduling_paused = True
        self.factory.save(update_fields=["scheduling_paused"])
        post = self._post()
        slot_at = timezone.now() + timedelta(minutes=1)
        self._attach_factory_slot(post, slot_at=slot_at)

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(post.status, "FAILED")
        self.assertIs(post.external_ids.get("slot_expired"), True)
        self.assertIn("A factory permaneceu pausada", post.error)
        self.assertEqual(result.get("status"), "FAILED")
        mock_up.assert_not_called()

    def test_factory_pausada_com_slot_ainda_valido_reagenda(self):
        """Contraste do teste acima: slot longe o bastante, o post é só adiado."""
        self.factory.scheduling_paused = True
        self.factory.save(update_fields=["scheduling_paused"])
        post = self._post()
        self._attach_factory_slot(post, slot_at=timezone.now() + timedelta(hours=2))

        result, mock_up, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(result, {"skipped": "factory scheduling paused"})
        self.assertEqual(post.status, "PENDING")
        self.assertNotIn("slot_expired", post.external_ids)
        mock_up.assert_not_called()


class RunPostToPlatformsHappyPathTests(RunPostToPlatformsFixtureMixin, TestCase):
    """Caminho feliz: publicação long-form no YouTube via Upload-Post."""

    def test_publicacao_bem_sucedida_deixa_o_post_em_done(self):
        """Estado final completo de uma publicação que deu certo."""
        post = self._post(retry_count=2, youtube_quota_retry_count=1, error="erro anterior")
        antes = timezone.now()

        result, mock_up, mock_native = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(post.status, "DONE")
        self.assertEqual(result.get("status"), "DONE")
        self.assertIsNotNone(post.posted_at)
        self.assertGreaterEqual(post.posted_at, antes)
        # Sucesso zera os dois contadores de retry e limpa o erro da tentativa anterior
        # (regressão do R-23: `error` está no `update_fields`, então um texto antigo era
        # gravado de volta junto com status=DONE e aparecia no painel).
        self.assertEqual(post.retry_count, 0)
        self.assertEqual(post.youtube_quota_retry_count, 0)
        self.assertEqual(post.error, "")
        # A API nativa do YouTube não é chamada quando o Upload-Post resolve.
        mock_up.assert_called_once()
        mock_native.assert_not_called()

    def test_publicacao_com_warning_preserva_o_texto_no_error(self):
        """Contraparte do R-23: o sucesso limpa o erro, mas NÃO engole warnings.

        Um warning do publisher vira o texto de `post.error` mesmo com `status=DONE` —
        é o canal que avisa "publicou, mas com ressalva". A limpeza do erro anterior não
        pode atropelar isso.
        """
        self.brand.upload_post_youtube_enabled = False
        self.brand.save(update_fields=["upload_post_youtube_enabled"])
        post = self._post(error="erro anterior")

        publisher = MagicMock()
        publisher.publish.return_value = {"video_id": "vid-nativo", "warning": "legenda cortada"}
        with patch(NATIVE_PUBLISHER_PATCH_TARGET, return_value=publisher):
            result = _run_post_to_platforms(post.id)

        post.refresh_from_db()
        self.assertEqual(post.status, "DONE")
        self.assertEqual(post.error, "YTB: legenda cortada")
        self.assertEqual(result.get("status"), "DONE")

    def test_publicacao_bem_sucedida_grava_o_fingerprint_do_arquivo(self):
        """O fingerprint é o sha256 do vídeo e é o que protege contra repost.

        Ele é calculado do arquivo em disco, não do banco — se o R-10 mover esse cálculo,
        o valor gravado precisa continuar sendo o hash do conteúdo.
        """
        post = self._post()

        self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(len(post.upload_fingerprint), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in post.upload_fingerprint))

    def test_publicacao_bem_sucedida_persiste_os_ids_do_provedor(self):
        """`external_ids` acumula o rastro do provedor e volta no retorno da função."""
        post = self._post()

        result, _, _ = self._run(post.id)

        post.refresh_from_db()
        self.assertEqual(post.external_ids.get("upload_post_request_id"), "req-r04")
        # Sem id de cliente: o provedor devolveu o dele.
        self.assertIsNone(post.external_ids.get("upload_post_client_request_id"))
        self.assertEqual(result.get("external_ids"), post.external_ids)
        self.assertEqual(result.get("errors"), [])

    def test_publicacao_bem_sucedida_registra_attempt_log_de_sucesso(self):
        """O slot da factory ganha um log SUCCESS com o `external_ids` final."""
        post = self._post()
        schedule = self._attach_factory_slot(post, slot_at=timezone.now() + timedelta(hours=2))

        self._run(post.id)

        post.refresh_from_db()
        log = FactoryPostingAttemptLog.objects.filter(posting_schedule=schedule).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.result, "SUCCESS")
        self.assertEqual(log.error_message, "")
        self.assertEqual(log.provider_response.get("external_ids"), post.external_ids)

    def test_publicacao_recebe_o_titulo_do_post_e_a_plataforma_esperada(self):
        """Contrato de chamada com o Upload-Post: um post YTB vira plataforma YOUTUBE."""
        post = self._post(title="Título que vai para o provedor")

        _, mock_up, _ = self._run(post.id)

        call_kwargs = mock_up.call_args.kwargs
        self.assertEqual(call_kwargs["platforms"], ["YOUTUBE"])
        self.assertIn("Título que vai para o provedor", str(call_kwargs))
