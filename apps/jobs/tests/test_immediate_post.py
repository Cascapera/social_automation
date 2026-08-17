"""Postar Imediato — o botão que envia agora e deixa o provedor publicar no horário do slot.

Três coisas são testadas aqui porque as três são invisíveis no código e caras em produção:

1. **A prévia e a execução usam o mesmo planejamento.** Se divergirem, o usuário confirma
   um número e outro acontece — e publicação não tem desfazer.
2. **Os campos do post.** `scheduled_at` no horário do slot e `privacy_status=private` são
   o que faz o YouTube receber `publishAt` e o Upload-Post receber `scheduled_date`. Com
   "agora" no lugar deles, os dois provedores publicam no minuto do clique — foi o bug de
   2026-08-17, e é o que o primeiro teste deste arquivo tranca.
3. **Slot vencido não entra** — decisão do usuário, e a que separa este botão do
   `enqueue_immediately` do agendamento.

O teste do agendamento normal continuar igual está em `test_factory_scheduler_retry.py` e
nos characterization tests; aqui a garantia é que a extração de `plan_brand_day` não mudou
o que o caminho antigo grava.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.brands.models import Brand, BrandSocialAccount, Factory
from apps.jobs.models import (
    DailyPostingPlan,
    DailyPostingPlanItem,
    FactoryPostingSchedule,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.jobs.services.immediate_post import (
    preview_immediate_post,
    run_immediate_post,
)


class ImmediatePostTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.factory = Factory.objects.create(name="Factory IP", timezone="America/Sao_Paulo")
        self.brand = Brand.objects.create(name="Brand IP", slug="brand-ip", factory=self.factory)
        self.brand.base_start_time = time(8, 0)
        self.brand.base_end_time = time(22, 0)
        self.brand.daily_min_posts = 2
        self.brand.daily_max_posts = 2
        self.brand.daily_min_long_posts = 0
        self.brand.daily_max_long_posts = 0
        self.brand.min_gap_minutes = 30
        self.brand.max_gap_minutes = 120
        self.brand.active_weekdays = [0, 1, 2, 3, 4, 5, 6]
        self.brand.scheduler_enabled = True
        self.brand.scheduler_paused = False
        self.brand.save()
        BrandSocialAccount.objects.create(brand=self.brand, platform="YT")

    def build_item(self, *, video_type="SHORT"):
        analysis = AutoCutAnalysis.objects.create(brand=self.brand, status="done")
        suggestion = AutoCutSuggestion.objects.create(
            analysis=analysis, cut_type="short", start_tc="00:10", end_tc="00:40"
        )
        corte = AutoCutCorte.objects.create(
            analysis=analysis, suggestion=suggestion, format="vertical", is_finalized=True
        )
        return VideoInventoryItem.objects.create(
            factory=self.factory,
            brand=self.brand,
            auto_cut_corte=corte,
            video_type=video_type,
            title="Vídeo de teste",
            status="AVAILABLE",
        )

    def build_plan_with_slots(self, day: date, slots_utc: list[datetime]) -> DailyPostingPlan:
        """Plano diário com slots explícitos, para o teste não depender do sorteio."""
        plan = DailyPostingPlan.objects.create(
            brand=self.brand,
            plan_date=day,
            timezone="America/Sao_Paulo",
            status=DailyPostingPlan.Status.GENERATED,
            planned_posts_count=len(slots_utc),
        )
        for index, slot in enumerate(slots_utc):
            DailyPostingPlanItem.objects.create(
                plan=plan,
                order_index=index,
                video_type="SHORT",
                scheduled_at=slot,
                status=DailyPostingPlanItem.Status.PLANNED,
            )
        return plan


class ImmediatePostFieldsTests(ImmediatePostTestCase):
    """Os campos que fazem o vídeo subir agora e ir ao ar no horário do slot."""

    def test_post_nasce_privado_e_no_horario_do_slot(self):
        """Regressão do bug de 2026-08-17: clicar no sábado escolhendo domingo publicava
        tudo no sábado, porque o post nascia com `scheduled_at=agora` e `public`.

        Com o horário do slot e `private`, `_get_publish_at` devolve o `publishAt` e o
        YouTube guarda o vídeo até a hora certa; `_format_scheduled_date` faz o mesmo no
        Upload-Post.
        """
        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        self.build_item()

        with patch("apps.social.tasks.process_brand_posting_queue_task.delay"):
            result = run_immediate_post(self.factory, target_date=day)

        self.assertEqual(result["queued"], 1)
        post = ScheduledPost.objects.get()

        self.assertEqual(post.privacy_status, "private")
        # No horário do slot (a menos do jitter), não no horário do clique.
        self.assertGreater(post.scheduled_at, timezone.now() + timedelta(hours=1))
        self.assertLess(abs(post.scheduled_at - slot), timedelta(minutes=10))

    def test_post_fica_marcado_como_envio_antecipado(self):
        """O marcador é o que impede o YouTube de sortear `public` direto para longos.

        Sem ele, 30% dos vídeos longos (`LONG_DIRECT_PUBLIC_PROBABILITY`) descartariam o
        `publishAt` e iriam ao ar na hora do upload — o bug de volta, em um a cada três.
        """
        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        self.build_item()

        with patch("apps.social.tasks.process_brand_posting_queue_task.delay"):
            run_immediate_post(self.factory, target_date=day)

        post = ScheduledPost.objects.get()
        self.assertTrue(post.external_ids.get("immediate_prepublish"))

    def test_agendamento_normal_nao_marca_envio_antecipado(self):
        """O caminho do beat continua participando do sorteio de longos."""
        from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory

        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        self.build_item()

        generate_daily_schedule_for_factory(self.factory, target_date=day, allow_rerun=True)

        post = ScheduledPost.objects.get()
        self.assertNotIn("immediate_prepublish", post.external_ids or {})

    def test_schedule_e_post_apontam_para_o_mesmo_horario_de_slot(self):
        """O deadline do slot e o horário de publicação são o mesmo instante.

        `_fail_expired_factory_slot` compara o relógio com `FactoryPostingSchedule.scheduled_at`
        antes de publicar. Como o upload acontece bem antes do slot, o deadline está sempre
        no futuro no momento do envio.
        """
        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        self.build_item()

        with patch("apps.social.tasks.process_brand_posting_queue_task.delay"):
            run_immediate_post(self.factory, target_date=day)

        schedule = FactoryPostingSchedule.objects.get()
        post = ScheduledPost.objects.get()
        self.assertEqual(schedule.scheduled_at, post.scheduled_at)
        # O deadline tem que estar no futuro, senão o post falha antes de sair.
        self.assertGreater(schedule.scheduled_at, timezone.now())

    def test_agendamento_normal_continua_privado_e_no_horario_do_slot(self):
        """A extração de plan_brand_day não pode ter mexido no caminho antigo."""
        from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory

        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        self.build_item()

        generate_daily_schedule_for_factory(
            self.factory,
            target_date=day,
            allow_rerun=True,
        )

        post = ScheduledPost.objects.get()
        self.assertEqual(post.privacy_status, "private")
        # Com jitter, mas ainda no horário do slot — não no presente.
        self.assertGreater(post.scheduled_at, timezone.now() + timedelta(hours=1))


class ImmediatePostSlotRulesTests(ImmediatePostTestCase):
    """Quais slots entram."""

    def test_slot_vencido_nao_entra(self):
        """Dois slots no mesmo dia, um de cada lado do relógio: só o futuro entra.

        O "agora" é fixado no meio dos dois. Sem isso o teste dependeria da hora em que a
        suíte roda — e passaria vazio metade do dia.
        """
        hoje = timezone.now().astimezone(UTC).date()
        # Fuso da factory é America/Sao_Paulo (UTC-3): 00h e 18h locais, com o agora às 09h.
        vencido = datetime.combine(hoje, time(3, 0), tzinfo=UTC)
        futuro = datetime.combine(hoje, time(21, 0), tzinfo=UTC)
        agora = datetime.combine(hoje, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(hoje, [vencido, futuro])
        self.build_item()
        self.build_item()

        with patch("apps.jobs.services.immediate_post.timezone.now", return_value=agora):
            preview = preview_immediate_post(self.factory, target_date=hoje)

        self.assertEqual(preview["total"], 1)
        slot_escolhido = preview["brands"][0]["slots"][0]
        self.assertEqual(slot_escolhido["scheduled_at"], futuro.astimezone(
            ZoneInfo("America/Sao_Paulo")
        ).isoformat())

    def test_dia_ja_agendado_nao_republica(self):
        """Clicar em Criar Agendamento e depois em Postar Imediato não duplica o dia."""
        from apps.jobs.services.factory_scheduler import generate_daily_schedule_for_factory

        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        self.build_item()

        generate_daily_schedule_for_factory(self.factory, target_date=day, allow_rerun=True)
        preview = preview_immediate_post(self.factory, target_date=day)

        self.assertEqual(preview["total"], 0)
        brand_payload = preview["brands"][0]
        # A prévia diz o motivo: o slot já tem agenda, não é falta de estoque.
        self.assertEqual(brand_payload["slots_already_scheduled"], 1)
        self.assertEqual(brand_payload["slots_without_stock"], 0)

    def test_sem_estoque_reporta_o_motivo(self):
        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        # Nenhum item de inventário criado.

        preview = preview_immediate_post(self.factory, target_date=day)

        self.assertEqual(preview["total"], 0)
        self.assertEqual(preview["brands"][0]["slots_without_stock"], 1)


class ImmediatePostPreviewMatchesRunTests(ImmediatePostTestCase):
    """A prévia é o contrato do botão: o número mostrado é o número publicado."""

    def test_previa_bate_com_a_execucao(self):
        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slots = [
            datetime.combine(day, time(12, 0), tzinfo=UTC),
            datetime.combine(day, time(18, 0), tzinfo=UTC),
        ]
        self.build_plan_with_slots(day, slots)
        self.build_item()
        self.build_item()

        preview = preview_immediate_post(self.factory, target_date=day)
        with patch("apps.social.tasks.process_brand_posting_queue_task.delay"):
            result = run_immediate_post(self.factory, target_date=day)

        self.assertEqual(preview["total"], 2)
        self.assertEqual(result["queued"], preview["total"])
        self.assertEqual(ScheduledPost.objects.count(), 2)

    def test_previa_nao_cria_post_nem_ocupa_inventario(self):
        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        item = self.build_item()

        preview_immediate_post(self.factory, target_date=day)

        self.assertEqual(ScheduledPost.objects.count(), 0)
        self.assertEqual(FactoryPostingSchedule.objects.count(), 0)
        item.refresh_from_db()
        self.assertEqual(item.status, "AVAILABLE")


class ImmediatePostQueueTests(ImmediatePostTestCase):
    """O envio reusa a fila do beat, não uma cópia."""

    def test_enfileira_a_task_de_publicacao_da_brand(self):
        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])
        self.build_item()

        with patch("apps.social.tasks.process_brand_posting_queue_task.delay") as mock_delay:
            # O disparo é registrado em transaction.on_commit; sem isto, o TestCase nunca
            # commita e o callback não roda — o teste passaria sem provar nada.
            with self.captureOnCommitCallbacks(execute=True):
                run_immediate_post(self.factory, target_date=day)

        mock_delay.assert_called_once()
        brand_id, post_ids = mock_delay.call_args[0]
        self.assertEqual(brand_id, self.brand.id)
        self.assertEqual(post_ids, [ScheduledPost.objects.get().id])

    def test_nada_enfileirado_quando_nao_ha_o_que_postar(self):
        day = timezone.now().astimezone(UTC).date() + timedelta(days=1)
        slot = datetime.combine(day, time(12, 0), tzinfo=UTC)
        self.build_plan_with_slots(day, [slot])

        with patch("apps.social.tasks.process_brand_posting_queue_task.delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                result = run_immediate_post(self.factory, target_date=day)

        mock_delay.assert_not_called()
        self.assertEqual(result["queued"], 0)
