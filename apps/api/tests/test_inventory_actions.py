"""Characterization tests das ações de inventário (refactor.md CT-3 / R-14, D-04).

`remove_awaiting` e `retry_posting` são regra de negócio e transação **dentro de handler
HTTP** (`apps/api/views.py`). O R-14 vai movê-las para
`apps/jobs/services/inventory_actions.py`, e o plano é explícito sobre a ordem: escrever
estes testes antes, contra o código atual, e vê-los passar de novo depois **sem edição**.

O que fica travado aqui é o **contrato HTTP**, porque é ele que o frontend consome:

  1. os códigos de status e as **mensagens de erro literais** — elas aparecem na tela;
  2. as **chaves e os valores do JSON de resposta** — o front lê `deleted_*` para montar o
     aviso de "removido", e `scheduled_for` para atualizar a lista;
  3. o **estado final no banco** das quatro entidades envolvidas (inventário, agendamento
     da factory, `ScheduledPost` e mídia do corte);
  4. **se a task de publicação foi enfileirada**, que é o efeito colateral que sai do
     processo.

⚠ `remove_awaiting` é destrutivo e irreversível: apaga linha e arquivo. Os testes de
contagem existem para que uma extração não apague a mais nem a menos — nenhum dos dois erros
aparece no código de status.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.brands.models import Brand, Factory
from apps.jobs.models import FactoryPostingSchedule, ScheduledPost, VideoInventoryItem

User = get_user_model()


class InventoryActionTestCase(TestCase):
    """Monta factory + brand + item de inventário, e o resto sob demanda."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="inv-user", password="securepass1")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.factory = Factory.objects.create(name="Factory CT3")
        self.brand = Brand.objects.create(name="Brand CT3", slug="brand-ct3", factory=self.factory)

    def build_corte(self):
        analysis = AutoCutAnalysis.objects.create(brand=self.brand, status="done")
        sug = AutoCutSuggestion.objects.create(
            analysis=analysis, cut_type="short", start_tc="00:10", end_tc="00:40"
        )
        return AutoCutCorte.objects.create(
            analysis=analysis, suggestion=sug, format="vertical", is_finalized=True
        )

    def build_item(self, *, status_item="AVAILABLE", video_type="SHORT", com_corte=True, **campos):
        return VideoInventoryItem.objects.create(
            factory=self.factory,
            brand=self.brand,
            auto_cut_corte=self.build_corte() if com_corte else None,
            video_type=video_type,
            title="Título do vídeo",
            status=status_item,
            **campos,
        )

    def build_post(self, corte, *, quando, status_post="PENDING"):
        return ScheduledPost.objects.create(
            job=None,
            auto_cut_corte=corte,
            platforms=["YT"],
            social_account=None,
            scheduled_at=quando,
            title="Título do vídeo",
            description="",
            privacy_status="private",
            status=status_post,
        )

    def build_schedule(self, item, *, quando, post=None, status_sched="FAILED"):
        return FactoryPostingSchedule.objects.create(
            factory=self.factory,
            brand=self.brand,
            inventory_item=item,
            video_type=item.video_type,
            scheduled_at=quando,
            status=status_sched,
            scheduled_post=post,
        )

    def remove_awaiting(self, item):
        return self.client.post(f"/api/video-inventory/{item.id}/remove-awaiting/", {}, format="json")

    def retry_posting(self, item, payload=None):
        return self.client.post(
            f"/api/video-inventory/{item.id}/retry-posting/", payload or {}, format="json"
        )


class RemoveAwaitingTests(InventoryActionTestCase):
    """A ação destrutiva: apaga inventário, agendamento, post e mídia."""

    def test_item_ja_postado_e_recusado_com_mensagem_propria(self):
        """A mensagem vai para a tela. `POSTED` é histórico, não fila."""
        item = self.build_item(status_item="POSTED")

        res = self.remove_awaiting(item)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "Não é possível remover um vídeo já postado.")
        self.assertTrue(VideoInventoryItem.objects.filter(id=item.id).exists())

    def test_remove_item_agendamento_e_post_de_uma_vez(self):
        item = self.build_item(status_item="SCHEDULED")
        post = self.build_post(item.auto_cut_corte, quando=timezone.now())
        schedule = self.build_schedule(item, quando=timezone.now(), post=post)

        res = self.remove_awaiting(item)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(
            res.data,
            {
                "ok": True,
                "deleted_inventory_item_id": item.id,
                "deleted_factory_schedule_count": 1,
                "deleted_scheduled_post_count": 1,
                "deleted_media_files": 0,
                "deleted_media_thumbnails": 0,
            },
        )
        self.assertFalse(VideoInventoryItem.objects.filter(id=item.id).exists())
        self.assertFalse(FactoryPostingSchedule.objects.filter(id=schedule.id).exists())
        self.assertFalse(ScheduledPost.objects.filter(id=post.id).exists())

    def test_item_sem_agendamento_sai_com_contagens_zeradas(self):
        """Item que nunca chegou a ser agendado também precisa sair do banco."""
        item = self.build_item()

        res = self.remove_awaiting(item)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["deleted_factory_schedule_count"], 0)
        self.assertEqual(res.data["deleted_scheduled_post_count"], 0)
        self.assertFalse(VideoInventoryItem.objects.filter(id=item.id).exists())

    def test_agendamento_sem_post_vinculado_nao_conta_post_apagado(self):
        """`FactoryPostingSchedule` pode existir sem `ScheduledPost`.

        Contar 1 aqui faria o aviso na tela mentir sobre o que foi apagado.
        """
        item = self.build_item()
        self.build_schedule(item, quando=timezone.now(), post=None)

        res = self.remove_awaiting(item)

        self.assertEqual(res.data["deleted_factory_schedule_count"], 1)
        self.assertEqual(res.data["deleted_scheduled_post_count"], 0)

    def test_o_corte_sobrevive_ao_item_removido(self):
        """A FK é `SET_NULL`: apagar o item do banco de vídeos **não** apaga o corte.

        O corte é o histórico da análise; some só a mídia dele.
        """
        item = self.build_item()
        corte_id = item.auto_cut_corte_id

        self.remove_awaiting(item)

        self.assertTrue(AutoCutCorte.objects.filter(id=corte_id).exists())


class RetryPostingTests(InventoryActionTestCase):
    """A ação de reativar a postagem: mexe em 3 entidades e enfileira a task."""

    def test_item_ja_postado_e_recusado(self):
        item = self.build_item(status_item="POSTED")

        res = self.retry_posting(item)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "Este vídeo já foi postado.")

    def test_item_sem_corte_e_sem_post_e_recusado(self):
        """Sem mídia não há o que publicar — e a mensagem diz isso, não 'erro interno'."""
        item = self.build_item(com_corte=False)

        res = self.retry_posting(item)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "Item sem corte/mídia vinculada para postagem.")

    def test_agendamento_ja_concluido_e_recusado(self):
        item = self.build_item(status_item="FAILED")
        post = self.build_post(item.auto_cut_corte, quando=timezone.now(), status_post="DONE")
        self.build_schedule(item, quando=timezone.now(), post=post)

        res = self.retry_posting(item)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "Este agendamento já foi concluído.")

    def test_reativa_as_tres_entidades_e_enfileira(self):
        item = self.build_item(status_item="FAILED", last_error="falhou antes")
        futuro = timezone.now() + timedelta(hours=3)
        post = self.build_post(item.auto_cut_corte, quando=futuro, status_post="FAILED")
        post.retry_count = 3
        post.error = "erro anterior"
        post.save(update_fields=["retry_count", "error"])
        schedule = self.build_schedule(item, quando=futuro, post=post)

        with patch("apps.social.tasks.post_to_platforms_task.delay") as enfileirar:
            res = self.retry_posting(item)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["ok"], True)
        self.assertEqual(res.data["inventory_item_id"], item.id)
        self.assertEqual(res.data["scheduled_post_id"], post.id)
        self.assertTrue(res.data["queued_immediately"])
        enfileirar.assert_called_once_with(post.id)

        post.refresh_from_db()
        schedule.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(post.retry_count, 0)
        self.assertEqual(post.error, "")
        self.assertIsNone(post.posted_at)
        self.assertEqual(schedule.status, "PLANNED")
        self.assertEqual(item.status, "SCHEDULED")
        self.assertEqual(item.last_error, "")

    def test_horario_ja_planejado_no_futuro_e_respeitado(self):
        """Não empurra para "agora + 30s" quem já tinha slot válido.

        Empurrar furaria a janela de publicação que o planejamento diário montou.
        """
        item = self.build_item(status_item="FAILED")
        futuro = timezone.now() + timedelta(hours=5)
        post = self.build_post(item.auto_cut_corte, quando=futuro)
        self.build_schedule(item, quando=futuro, post=post)

        with patch("apps.social.tasks.post_to_platforms_task.delay"):
            res = self.retry_posting(item)

        post.refresh_from_db()
        self.assertEqual(post.scheduled_at, futuro)
        self.assertEqual(res.data["scheduled_for"], futuro)

    def test_horario_planejado_no_passado_vira_agora_mais_30s(self):
        item = self.build_item(status_item="FAILED")
        passado = timezone.now() - timedelta(hours=2)
        post = self.build_post(item.auto_cut_corte, quando=passado)
        self.build_schedule(item, quando=passado, post=post)

        antes = timezone.now()
        with patch("apps.social.tasks.post_to_platforms_task.delay"):
            self.retry_posting(item)

        post.refresh_from_db()
        self.assertGreater(post.scheduled_at, antes)
        self.assertLess(post.scheduled_at, antes + timedelta(minutes=1))

    def test_scheduled_at_do_payload_reagenda(self):
        """O front manda `scheduled_at` para reagendar sem passar pelo planejamento."""
        item = self.build_item(status_item="FAILED")
        post = self.build_post(item.auto_cut_corte, quando=timezone.now())
        self.build_schedule(item, quando=timezone.now(), post=post)
        novo = (timezone.now() + timedelta(days=1)).replace(microsecond=0)

        with patch("apps.social.tasks.post_to_platforms_task.delay"):
            self.retry_posting(item, {"scheduled_at": novo.isoformat()})

        post.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(post.scheduled_at, novo)
        self.assertEqual(item.scheduled_for, novo)

    def test_scheduled_at_ilegivel_cai_no_horario_planejado(self):
        """Data inválida não derruba a ação: volta para o slot que já existia."""
        item = self.build_item(status_item="FAILED")
        futuro = timezone.now() + timedelta(hours=4)
        post = self.build_post(item.auto_cut_corte, quando=futuro)
        self.build_schedule(item, quando=futuro, post=post)

        with patch("apps.social.tasks.post_to_platforms_task.delay"):
            res = self.retry_posting(item, {"scheduled_at": "não é data"})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        post.refresh_from_db()
        self.assertEqual(post.scheduled_at, futuro)

    def test_item_sem_post_cria_agendamento_para_o_corte(self):
        """Caminho do item que nunca foi agendado: cria `ScheduledPost` e o schedule."""
        item = self.build_item(status_item="AVAILABLE")

        with patch("apps.social.tasks.post_to_platforms_task.delay") as enfileirar:
            res = self.retry_posting(item)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        post = ScheduledPost.objects.get(id=res.data["scheduled_post_id"])
        self.assertEqual(post.auto_cut_corte_id, item.auto_cut_corte_id)
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(post.privacy_status, "private")
        self.assertTrue(FactoryPostingSchedule.objects.filter(inventory_item=item).exists())
        enfileirar.assert_called_once_with(post.id)

    def test_plataforma_sai_do_tipo_do_video(self):
        """`SHORT` → `YT`, `LONG` → `YTB`. Trocar aqui publica no lugar errado."""
        curto = self.build_item(video_type="SHORT")
        longo = self.build_item(video_type="LONG")

        with patch("apps.social.tasks.post_to_platforms_task.delay"):
            res_curto = self.retry_posting(curto)
            res_longo = self.retry_posting(longo)

        self.assertEqual(
            ScheduledPost.objects.get(id=res_curto.data["scheduled_post_id"]).platforms, ["YT"]
        )
        self.assertEqual(
            ScheduledPost.objects.get(id=res_longo.data["scheduled_post_id"]).platforms, ["YTB"]
        )

    def test_schedule_existente_sem_post_e_reaproveitado(self):
        """Não cria um segundo `FactoryPostingSchedule` para o mesmo item."""
        item = self.build_item(status_item="FAILED")
        schedule = self.build_schedule(item, quando=timezone.now(), post=None)

        with patch("apps.social.tasks.post_to_platforms_task.delay"):
            res = self.retry_posting(item)

        self.assertEqual(FactoryPostingSchedule.objects.filter(inventory_item=item).count(), 1)
        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_post_id, res.data["scheduled_post_id"])
        self.assertEqual(schedule.status, "PLANNED")
