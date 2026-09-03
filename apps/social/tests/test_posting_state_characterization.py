"""Characterization tests da máquina de estados de publicação (refactor.md R-03 / D-02).

A transição "este vídeo foi publicado" coordena 4 modelos — ScheduledPost,
FactoryPostingSchedule, VideoInventoryItem e PostedVideoLog — e até o R-07 estava escrita
em **cinco lugares diferentes**, sem dono:

  A. apps/social/tasks.py  _sync_factory_posting_schedule, ramo YouTube-only
  B. apps/social/tasks.py  _sync_factory_posting_schedule, ramo demais plataformas
  C. apps/social/tasks.py  reconciliação (era _mark_factory_posting_verified)
  D. apps/api/views.py     VideoInventoryItemViewSet.mark_posted (ação HTTP)
  E. apps/social/management/commands/fix_youtube_posted_status.py

Estes testes nasceram no R-03 **fixando o comportamento de cada cópia**, inclusive o
esquisito e o que parecia bug, para que R-06 e R-07 pudessem unificá-las sem mudar nada
sem querer. Eles continuam entrando por cada um dos cinco pontos de chamada — a cobertura
não encolheu — mas as cinco entradas agora desembocam em apps/social/services/
posting_state.py, e por isso este arquivo passou a afirmar o comportamento **unificado**.

As 5 divergências que o R-03 mediu foram resolvidas no R-07 (decisões em L-7, 2026-08-13).
Cada uma tem aqui o teste que inverteu, com o docstring dizendo o que mudou:

  1. B criava PostedVideoLog SEM deduplicar e SEM exigir external_video_id.
     → agora todos deduplicam e nenhum grava log com id vazio. Era bug, não intenção.
  2. D não zerava schedule.next_retry_at.        → agora todos zeram.
  3. D e E não atualizavam attempt_count.        → agora todos sincronizam com o post.
  4. Só E preservava item.posted_at/scheduled_for já existentes.  → virou a regra geral.
  5. Só C levava o ScheduledPost a DONE.         → agora todos levam (no-op onde já está).

O teste que fecha o arquivo, PostedTransitionConvergenceTests, é o que trava o ganho: as
quatro entradas produzem o mesmo estado final. Era ele que, no R-03, provava o contrário.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brands.models import Brand, Factory
from apps.jobs.models import (
    FactoryPostingSchedule,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.social.services.posting_state import mark_posted, mark_still_scheduled
from apps.social.tasks import _sync_factory_posting_schedule


class PostingStateFixtureMixin:
    """Monta factory + brand + item + post + schedule ligados entre si."""

    def build_chain(
        self,
        *,
        platforms=None,
        post_status="DONE",
        external_ids=None,
        item_status="SCHEDULED",
        retry_count=0,
        item_posted_at=None,
        item_scheduled_for=None,
        daily_plan_item=None,
    ):
        n = getattr(self, "_chain_seq", 0) + 1
        self._chain_seq = n
        factory = Factory.objects.create(name=f"Factory Caracterizacao {n}")
        brand = Brand.objects.create(
            name=f"Brand Caracterizacao {n}", slug=f"brand-caracterizacao-{n}", factory=factory
        )
        item = VideoInventoryItem.objects.create(
            factory=factory,
            brand=brand,
            video_type="SHORT",
            status=item_status,
            title="Video de caracterizacao",
            posted_at=item_posted_at,
            scheduled_for=item_scheduled_for,
        )
        self.scheduled_at = timezone.now() - timedelta(minutes=5)
        self.post_posted_at = timezone.now() - timedelta(minutes=1)
        post = ScheduledPost.objects.create(
            scheduled_at=self.scheduled_at,
            platforms=platforms if platforms is not None else ["YTB"],
            status=post_status,
            external_ids=external_ids if external_ids is not None else {"YTB": "vid-123"},
            retry_count=retry_count,
            posted_at=self.post_posted_at if post_status == "DONE" else None,
        )
        schedule = FactoryPostingSchedule.objects.create(
            factory=factory,
            brand=brand,
            inventory_item=item,
            video_type="SHORT",
            scheduled_at=self.scheduled_at,
            status="PLANNED",
            scheduled_post=post,
            next_retry_at=timezone.now() + timedelta(minutes=30),
            daily_plan_item=daily_plan_item,
        )
        return factory, brand, item, post, schedule


class SyncFactoryPostingScheduleYouTubeTests(PostingStateFixtureMixin, TestCase):
    """Cópia A — _sync_factory_posting_schedule, ramo YouTube-only (tasks.py:414)."""

    def test_marks_schedule_item_and_creates_log(self):
        _factory, _brand, item, post, schedule = self.build_chain(
            platforms=["YTB"], retry_count=2
        )

        _sync_factory_posting_schedule(post)

        schedule.refresh_from_db()
        item.refresh_from_db()
        post.refresh_from_db()

        self.assertEqual(schedule.status, "DONE")
        self.assertEqual(schedule.attempt_count, 2)
        self.assertIsNone(schedule.next_retry_at)

        self.assertEqual(item.status, "POSTED")
        self.assertEqual(item.posted_at, self.post_posted_at)
        self.assertEqual(item.scheduled_for, self.scheduled_at)
        self.assertEqual(item.last_error, "")
        self.assertEqual(item.attempt_count, 2)

        # O ScheduledPost NÃO é tocado por esta cópia — ela reage a um post já DONE.
        self.assertEqual(post.status, "DONE")

        log = PostedVideoLog.objects.get(inventory_item=item)
        self.assertEqual(log.external_platform, "YTB")
        self.assertEqual(log.external_video_id, "vid-123")
        self.assertEqual(log.posted_at, self.post_posted_at)
        self.assertEqual(log.metadata_snapshot["scheduled_post_id"], post.id)

    def test_is_idempotent_on_second_call(self):
        """Ramo YouTube deduplica: chamar duas vezes não cria log duplicado."""
        _factory, _brand, item, post, _schedule = self.build_chain(platforms=["YTB"])

        _sync_factory_posting_schedule(post)
        _sync_factory_posting_schedule(post)

        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 1)

    def test_without_external_video_id_creates_no_log(self):
        """Ramo YouTube exige external_video_id preenchido para registrar o log."""
        _factory, _brand, item, post, _schedule = self.build_chain(
            platforms=["YTB"], external_ids={}
        )

        _sync_factory_posting_schedule(post)

        item.refresh_from_db()
        self.assertEqual(item.status, "POSTED")  # item vira POSTED mesmo assim
        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 0)

    def test_returns_silently_when_post_has_no_schedule(self):
        """Comportamento esquisito preservado: sem schedule, sai sem fazer nada."""
        post = ScheduledPost.objects.create(
            scheduled_at=timezone.now(), platforms=["YTB"], status="DONE"
        )

        _sync_factory_posting_schedule(post)  # não levanta

        self.assertEqual(PostedVideoLog.objects.count(), 0)


class SyncFactoryPostingScheduleNonYouTubeTests(PostingStateFixtureMixin, TestCase):
    """Cópia B — _sync_factory_posting_schedule, demais plataformas (tasks.py:452)."""

    def test_marks_schedule_item_and_creates_log(self):
        _factory, _brand, item, post, schedule = self.build_chain(
            platforms=["TIKTOK"], external_ids={"TIKTOK": "tt-999"}, retry_count=1
        )

        _sync_factory_posting_schedule(post)

        schedule.refresh_from_db()
        item.refresh_from_db()

        self.assertEqual(schedule.status, "DONE")
        self.assertEqual(schedule.attempt_count, 1)
        self.assertIsNone(schedule.next_retry_at)

        self.assertEqual(item.status, "POSTED")
        self.assertEqual(item.attempt_count, 1)

        log = PostedVideoLog.objects.get(inventory_item=item)
        self.assertEqual(log.external_platform, "TIKTOK")
        self.assertEqual(log.external_video_id, "tt-999")

    def test_second_call_does_not_duplicate_the_log(self):
        """DIVERGÊNCIA 1 — INVERTIDO no R-07.

        Este era o único ramo que não deduplicava: duas sincronizações do mesmo post
        geravam duas linhas em PostedVideoLog. O R-03 fixou esse comportamento e o
        classificou como bug, não intenção — não há motivo para o ramo não-YouTube
        auditar em dobro. Agora ele passa pelo posting_state e deduplica como os outros.
        """
        _factory, _brand, item, post, _schedule = self.build_chain(
            platforms=["TIKTOK"], external_ids={"TIKTOK": "tt-999"}
        )

        _sync_factory_posting_schedule(post)
        _sync_factory_posting_schedule(post)

        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 1)

    def test_without_external_id_creates_no_log(self):
        """DIVERGÊNCIA 1 (parte 2) — INVERTIDO no R-07.

        Sem guard de id vazio, este ramo gravava um log com external_video_id em branco:
        uma linha que não audita nada e ainda envenena a deduplicação das chamadas
        seguintes. O item continua virando POSTED — o que sumiu é só a linha inútil.
        """
        _factory, _brand, item, post, _schedule = self.build_chain(
            platforms=["TIKTOK"], external_ids={}
        )

        _sync_factory_posting_schedule(post)

        item.refresh_from_db()
        self.assertEqual(item.status, "POSTED")
        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 0)


class SyncFactoryPostingScheduleFailureTests(PostingStateFixtureMixin, TestCase):
    """_sync_factory_posting_schedule nos ramos FAILED e retry (tasks.py:481-504)."""

    def test_failed_standalone_schedule_marks_item_failed(self):
        _factory, _brand, item, post, schedule = self.build_chain(
            post_status="FAILED", retry_count=3
        )
        post.error = "estourou a quota"
        post.save(update_fields=["error"])

        _sync_factory_posting_schedule(post)

        schedule.refresh_from_db()
        item.refresh_from_db()

        self.assertEqual(schedule.status, "FAILED")
        self.assertEqual(schedule.attempt_count, 3)
        self.assertIsNone(schedule.next_retry_at)
        # Sem daily_plan_item, o item é "standalone" e vai para FAILED.
        self.assertEqual(item.status, "FAILED")
        self.assertEqual(item.scheduled_for, schedule.scheduled_at)
        self.assertEqual(item.last_error, "estourou a quota")
        self.assertEqual(item.attempt_count, 3)

    def test_pending_with_retry_reschedules(self):
        _factory, _brand, item, post, schedule = self.build_chain(
            post_status="PENDING", retry_count=1
        )

        _sync_factory_posting_schedule(post)

        schedule.refresh_from_db()
        item.refresh_from_db()

        self.assertEqual(schedule.status, "PLANNED")
        self.assertEqual(schedule.attempt_count, 1)
        self.assertEqual(schedule.next_retry_at, post.scheduled_at)
        self.assertEqual(item.status, "SCHEDULED")
        self.assertEqual(item.attempt_count, 1)

    def test_pending_without_retry_changes_nothing(self):
        """Primeira tentativa ainda pendente: nenhuma das guardas casa, nada muda."""
        _factory, _brand, item, post, schedule = self.build_chain(
            post_status="PENDING", retry_count=0
        )
        status_antes = schedule.status
        next_retry_antes = schedule.next_retry_at

        _sync_factory_posting_schedule(post)

        schedule.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(schedule.status, status_antes)
        self.assertEqual(schedule.next_retry_at, next_retry_antes)
        self.assertEqual(item.status, "SCHEDULED")


class MarkFactoryPostingVerifiedTests(PostingStateFixtureMixin, TestCase):
    """Cópia C — `posting_state.mark_posted`, extraída de `tasks.py` no R-06."""

    def test_drives_post_to_done_and_marks_everything(self):
        _factory, _brand, item, post, schedule = self.build_chain(
            post_status="PENDING", retry_count=2
        )
        post.error = "erro anterior que deve ser limpo"
        post.save(update_fields=["error"])

        mark_posted(
            post,
            platform="YT",
            external_video_id="vid-abc",
            log_metadata={"youtube_verify": {"checked": True}},
        )

        post.refresh_from_db()
        schedule.refresh_from_db()
        item.refresh_from_db()

        # DIVERGÊNCIA 5 — desde o R-07 toda entrada leva o ScheduledPost a DONE.
        self.assertEqual(post.status, "DONE")
        self.assertIsNotNone(post.posted_at)
        self.assertEqual(post.error, "")

        self.assertEqual(schedule.status, "DONE")
        self.assertEqual(schedule.attempt_count, 2)
        self.assertIsNone(schedule.next_retry_at)

        self.assertEqual(item.status, "POSTED")
        self.assertEqual(item.posted_at, post.posted_at)
        self.assertEqual(item.scheduled_for, post.scheduled_at)
        self.assertEqual(item.last_error, "")
        self.assertEqual(item.attempt_count, 2)

        log = PostedVideoLog.objects.get(inventory_item=item)
        self.assertEqual(log.external_platform, "YT")
        self.assertEqual(log.external_video_id, "vid-abc")
        self.assertEqual(log.metadata_snapshot["youtube_verify"], {"checked": True})

    def test_preserves_existing_post_posted_at(self):
        _factory, _brand, _item, post, _schedule = self.build_chain(post_status="DONE")
        posted_at_original = post.posted_at

        mark_posted(post, platform="YT", external_video_id="vid-abc")

        post.refresh_from_db()
        self.assertEqual(post.posted_at, posted_at_original)

    def test_is_idempotent_on_second_call(self):
        _factory, _brand, item, post, _schedule = self.build_chain(post_status="PENDING")

        mark_posted(post, platform="YT", external_video_id="vid-abc")
        mark_posted(post, platform="YT", external_video_id="vid-abc")

        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 1)

    def test_creates_no_log_when_video_id_is_blank(self):
        """DIVERGÊNCIA 1 (parte 3) — INVERTIDO no R-07.

        C deduplicava mas não validava id vazio. A regra canônica é a de A: sem id
        externo não há o que auditar. O resto da transição acontece normalmente.
        """
        _factory, _brand, item, post, _schedule = self.build_chain(post_status="PENDING")

        mark_posted(post, platform="YT", external_video_id="")

        item.refresh_from_db()
        self.assertEqual(item.status, "POSTED")
        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 0)

    def test_returns_silently_when_post_has_no_schedule(self):
        """Comportamento esquisito preservado (tasks.py:831): sem schedule, o post NÃO
        vira DONE — a transição inteira é abandonada em silêncio."""
        post = ScheduledPost.objects.create(
            scheduled_at=timezone.now(), platforms=["YTB"], status="PENDING"
        )

        mark_posted(post, platform="YT", external_video_id="vid-abc")

        post.refresh_from_db()
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(PostedVideoLog.objects.count(), 0)

    def test_transition_is_atomic(self):
        """D-03 / R-06 — as 4 escritas estão em `transaction.atomic()`.

        Esta asserção era o inverso até o R-06: o teste do R-03 fixava a **ausência** de
        atomicidade e dizia, no próprio docstring, "R-06 deve INVERTER esta asserção".
        É esta a inversão.

        Se a criação do `PostedVideoLog` — a última das 4 escritas — falhar, nenhuma das
        três anteriores pode sobreviver. O estado parcial era exatamente o que produzia
        `ScheduledPost` em DONE com `VideoInventoryItem` ainda em SCHEDULED, a
        inconsistência que obrigou a existir o comando `fix_youtube_posted_status`.
        """
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")
        status_inicial = (post.status, schedule.status, item.status)

        with self.assertRaises(RuntimeError):
            original_create = PostedVideoLog.objects.create

            def explode(*args, **kwargs):
                raise RuntimeError("falha simulada no ultimo passo")

            PostedVideoLog.objects.create = explode
            try:
                mark_posted(post, platform="YT", external_video_id="vid-abc")
            finally:
                PostedVideoLog.objects.create = original_create

        post.refresh_from_db()
        schedule.refresh_from_db()
        item.refresh_from_db()

        # Nada persistiu: os 3 modelos continuam como estavam antes da tentativa.
        self.assertEqual((post.status, schedule.status, item.status), status_inicial)
        self.assertEqual(PostedVideoLog.objects.count(), 0)


class MarkFactoryPostingStillScheduledTests(PostingStateFixtureMixin, TestCase):
    """`posting_state.mark_still_scheduled` — 2 modelos, atômica desde o R-06."""

    def test_keeps_item_scheduled_with_next_check(self):
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")

        mark_still_scheduled(post, publish_at_raw=None, note="")

        schedule.refresh_from_db()
        item.refresh_from_db()

        self.assertEqual(schedule.status, "PLANNED")
        self.assertIsNotNone(schedule.next_retry_at)
        self.assertEqual(item.status, "SCHEDULED")
        self.assertEqual(
            item.last_error, "Agendado no YouTube. Aguardando publicação no canal."
        )

    def test_publish_at_pushes_next_check_past_publication(self):
        _factory, _brand, _item, post, schedule = self.build_chain(post_status="PENDING")
        publish_at = timezone.now() + timedelta(hours=3)

        mark_still_scheduled(
            post, publish_at_raw=publish_at.isoformat(), note="agendado"
        )

        schedule.refresh_from_db()
        self.assertGreater(schedule.next_retry_at, publish_at)


class MarkPostedApiActionTests(PostingStateFixtureMixin, TestCase):
    """Cópia D — VideoInventoryItemViewSet.mark_posted (views.py:1447)."""

    def setUp(self):
        self.user = User.objects.create_user(username="marker", password="senha-longa-1")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_marks_post_schedule_item_and_manual_log(self):
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")

        resp = self.client.post(f"/api/video-inventory/{item.id}/mark-posted/", {}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["ok"])

        post.refresh_from_db()
        schedule.refresh_from_db()
        item.refresh_from_db()

        self.assertEqual(post.status, "DONE")
        self.assertEqual(post.error, "")
        self.assertEqual(schedule.status, "DONE")
        self.assertEqual(item.status, "POSTED")
        self.assertEqual(item.last_error, "")

        log = PostedVideoLog.objects.get(inventory_item=item)
        self.assertEqual(log.external_platform, "MANUAL")
        self.assertEqual(log.external_video_id, "manual")
        self.assertTrue(log.metadata_snapshot["manual_post"])

    def test_clears_schedule_next_retry_at(self):
        """DIVERGÊNCIA 2 — INVERTIDO no R-07.

        A marcação manual era a única que deixava next_retry_at preenchido num schedule
        DONE. Estado sujo: o varredor de retry pode voltar a olhar para ele.
        """
        _factory, _brand, item, _post, schedule = self.build_chain(post_status="PENDING")
        self.assertIsNotNone(schedule.next_retry_at)

        self.client.post(f"/api/video-inventory/{item.id}/mark-posted/", {}, format="json")

        schedule.refresh_from_db()
        self.assertIsNone(schedule.next_retry_at)

    def test_syncs_attempt_count_and_fills_scheduled_for(self):
        """DIVERGÊNCIAS 3 e 4 — INVERTIDO no R-07.

        A marcação manual não sincronizava attempt_count nem preenchia scheduled_for.
        Não sincronizar não preservava histórico nenhum: deixava o valor velho da última
        sincronização automática, que é mentira. O número real está no post.

        `scheduled_for` é preenchido porque estava **vazio** — a regra 4 preserva o que
        já existe, e aqui não existia nada a preservar.
        """
        _factory, _brand, item, post, schedule = self.build_chain(
            post_status="PENDING", retry_count=4
        )
        item.attempt_count = 0
        item.scheduled_for = None
        item.save(update_fields=["attempt_count", "scheduled_for"])

        self.client.post(f"/api/video-inventory/{item.id}/mark-posted/", {}, format="json")

        item.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(item.attempt_count, 4)
        self.assertEqual(item.scheduled_for, post.scheduled_at)
        self.assertEqual(schedule.attempt_count, 4)

    def test_rejects_item_already_posted(self):
        _factory, _brand, item, _post, _schedule = self.build_chain(item_status="POSTED")

        resp = self.client.post(f"/api/video-inventory/{item.id}/mark-posted/", {}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("já está marcado como postado", resp.data["error"])

    def test_accepts_explicit_posted_at(self):
        _factory, _brand, item, _post, _schedule = self.build_chain(post_status="PENDING")
        quando = (timezone.now() - timedelta(days=2)).replace(microsecond=0)

        resp = self.client.post(
            f"/api/video-inventory/{item.id}/mark-posted/",
            {"posted_at": quando.isoformat()},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.posted_at, quando)

    def test_rejects_invalid_posted_at(self):
        _factory, _brand, item, _post, _schedule = self.build_chain(post_status="PENDING")

        resp = self.client.post(
            f"/api/video-inventory/{item.id}/mark-posted/",
            {"posted_at": "nao-e-data"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.status, "SCHEDULED")


class FixYouTubePostedStatusCommandTests(PostingStateFixtureMixin, TestCase):
    """Cópia E — management command fix_youtube_posted_status (linha 47)."""

    def test_marks_pending_item_whose_post_is_already_done(self):
        _factory, _brand, item, post, schedule = self.build_chain(
            post_status="DONE", external_ids={"YT": "yt-777"}, item_status="SCHEDULED"
        )

        call_command("fix_youtube_posted_status")

        schedule.refresh_from_db()
        item.refresh_from_db()

        self.assertEqual(schedule.status, "DONE")
        self.assertIsNone(schedule.next_retry_at)
        self.assertEqual(item.status, "POSTED")

        log = PostedVideoLog.objects.get(inventory_item=item)
        self.assertEqual(log.external_platform, "YT")
        self.assertEqual(log.external_video_id, "yt-777")

    def test_PRESERVES_existing_posted_at_and_scheduled_for(self):
        """DIVERGÊNCIA 4 — E preserva o que já existe; A, B, C e D sobrescrevem."""
        ja_postado_em = (timezone.now() - timedelta(days=5)).replace(microsecond=0)
        ja_agendado_para = (timezone.now() - timedelta(days=6)).replace(microsecond=0)
        _factory, _brand, item, _post, _schedule = self.build_chain(
            post_status="DONE",
            external_ids={"YT": "yt-777"},
            item_posted_at=ja_postado_em,
            item_scheduled_for=ja_agendado_para,
        )

        call_command("fix_youtube_posted_status")

        item.refresh_from_db()
        self.assertEqual(item.posted_at, ja_postado_em)
        self.assertEqual(item.scheduled_for, ja_agendado_para)

    def test_syncs_attempt_count(self):
        """DIVERGÊNCIA 3 — INVERTIDO no R-07.

        O comando de reparo também não sincronizava attempt_count. Como ele existe
        justamente para consertar estado que ficou errado, deixar um contador velho para
        trás era o oposto do propósito dele.
        """
        _factory, _brand, item, _post, schedule = self.build_chain(
            post_status="DONE", external_ids={"YT": "yt-777"}, retry_count=3
        )

        call_command("fix_youtube_posted_status")

        item.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(item.attempt_count, 3)
        self.assertEqual(schedule.attempt_count, 3)

    def test_skips_post_without_youtube_external_id(self):
        _factory, _brand, item, _post, _schedule = self.build_chain(
            post_status="DONE", external_ids={"TIKTOK": "tt-1"}
        )

        call_command("fix_youtube_posted_status")

        item.refresh_from_db()
        self.assertEqual(item.status, "SCHEDULED")
        self.assertEqual(PostedVideoLog.objects.count(), 0)

    def test_skips_item_already_posted(self):
        _factory, _brand, item, _post, _schedule = self.build_chain(
            post_status="DONE", external_ids={"YT": "yt-777"}, item_status="POSTED"
        )

        call_command("fix_youtube_posted_status")

        self.assertEqual(PostedVideoLog.objects.count(), 0)

    def test_is_idempotent_on_second_run(self):
        _factory, _brand, item, _post, _schedule = self.build_chain(
            post_status="DONE", external_ids={"YT": "yt-777"}
        )

        call_command("fix_youtube_posted_status")
        item.refresh_from_db()
        item.status = "SCHEDULED"  # força reprocessamento
        item.save(update_fields=["status"])
        call_command("fix_youtube_posted_status")

        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 1)


class PostedTransitionConvergenceTests(PostingStateFixtureMixin, TestCase):
    """O ganho do R-07, travado em teste: as entradas convergem para o mesmo estado.

    No R-03 esta classe se chamava ...DivergenceTests e o teste único afirmava que as
    cópias só concordavam em **três campos**. É a inversão que dá nome ao item.
    """

    def test_all_entry_points_produce_the_same_final_state(self):
        retry = 2
        # A — YouTube
        _f, _b, item_a, post_a, sched_a = self.build_chain(platforms=["YTB"], retry_count=retry)
        _sync_factory_posting_schedule(post_a)

        # B — não-YouTube
        _f, _b, item_b, post_b, sched_b = self.build_chain(
            platforms=["TIKTOK"], external_ids={"TIKTOK": "tt-1"}, retry_count=retry
        )
        _sync_factory_posting_schedule(post_b)

        # C — reconciliação
        _f, _b, item_c, post_c, sched_c = self.build_chain(
            post_status="PENDING", retry_count=retry
        )
        mark_posted(post_c, platform="YT", external_video_id="vid-c")

        # E — management command
        _f, _b, item_e, post_e, sched_e = self.build_chain(
            post_status="DONE", external_ids={"YT": "yt-e"}, retry_count=retry
        )
        call_command("fix_youtube_posted_status")

        for rotulo, post, item, schedule in (
            ("A", post_a, item_a, sched_a),
            ("B", post_b, item_b, sched_b),
            ("C", post_c, item_c, sched_c),
            ("E", post_e, item_e, sched_e),
        ):
            with self.subTest(copia=rotulo):
                post.refresh_from_db()
                item.refresh_from_db()
                schedule.refresh_from_db()

                self.assertEqual(post.status, "DONE")          # divergência 5
                self.assertEqual(post.error, "")
                self.assertIsNotNone(post.posted_at)

                self.assertEqual(schedule.status, "DONE")
                self.assertIsNone(schedule.next_retry_at)      # divergência 2
                self.assertEqual(schedule.attempt_count, retry)  # divergência 3

                self.assertEqual(item.status, "POSTED")
                self.assertEqual(item.last_error, "")
                self.assertEqual(item.attempt_count, retry)    # divergência 3
                self.assertEqual(item.posted_at, post.posted_at)  # divergência 4
                self.assertEqual(item.scheduled_for, post.scheduled_at)

                # divergência 1 — exatamente um log, nunca zero, nunca dois
                self.assertEqual(
                    PostedVideoLog.objects.filter(inventory_item=item).count(), 1
                )

    def test_posting_state_is_the_only_writer_of_posted_status(self):
        """Anti-drift do D-02: uma sexta cópia começa exatamente assim.

        O critério de validação do R-07, escrito no refactor.md, é que
        `grep 'status = "POSTED"' apps/` retorne **um** site. Este teste é esse grep,
        rodando no CI — se alguém voltar a escrever a transição à mão em vez de chamar
        o dono, o build quebra em vez de a duplicação passar despercebida na revisão.

        O padrão só casa **atribuição a atributo** (`item.status = "POSTED"`), que é
        como as cinco cópias escreviam. `filter(status="POSTED")` é leitura e não conta.
        """
        atribuicao = re.compile(r'\.status\s*=\s*"POSTED"')
        raiz = Path(__file__).resolve().parents[3] / "apps"
        dono = raiz / "social" / "services" / "posting_state.py"

        # Se o padrão parar de casar com o próprio dono, ele deixou de valer alguma coisa
        # e este teste passaria vazio para sempre.
        self.assertRegex(dono.read_text(encoding="utf-8"), atribuicao)

        culpados = sorted(
            caminho.relative_to(raiz).as_posix()
            for caminho in raiz.rglob("*.py")
            if caminho != dono
            and "tests" not in caminho.parts
            and atribuicao.search(caminho.read_text(encoding="utf-8"))
        )

        self.assertEqual(
            culpados,
            [],
            "Transição de publicação escrita fora do posting_state — ver D-02/R-07 no "
            f"refactor.md. Arquivos: {culpados}",
        )
