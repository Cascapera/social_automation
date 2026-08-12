"""Characterization tests da máquina de estados de publicação (refactor.md R-03 / D-02).

A transição "este vídeo foi publicado" coordena 4 modelos — ScheduledPost,
FactoryPostingSchedule, VideoInventoryItem e PostedVideoLog — e hoje está escrita em
**cinco lugares diferentes**, sem dono:

  A. apps/social/tasks.py:414  _sync_factory_posting_schedule, ramo YouTube-only
  B. apps/social/tasks.py:452  _sync_factory_posting_schedule, ramo demais plataformas
  C. apps/social/tasks.py:823  _mark_factory_posting_verified
  D. apps/api/views.py:1447    VideoInventoryItemViewSet.mark_posted (ação HTTP)
  E. apps/social/management/commands/fix_youtube_posted_status.py:47

Estes testes NÃO julgam o comportamento: eles **fixam o comportamento atual**, inclusive
o esquisito e o que parece bug, para que R-06 e R-07 possam unificar as cinco cópias sem
mudar nada sem querer. Onde o comportamento atual é suspeito, o teste diz isso no nome e
no comentário — mas continua afirmando o que o código faz hoje.

Divergências que estes testes travam (decisão de qual é o certo fica para R-07):

  1. B cria PostedVideoLog SEM deduplicar e SEM exigir external_video_id;
     A, C, D e E deduplicam. B é o único que gera log duplicado e log com id vazio.
  2. D não zera schedule.next_retry_at; A, C e E zeram.
  3. D e E não atualizam attempt_count; A, B e C atualizam.
  4. A, B, C e D sobrescrevem item.posted_at / item.scheduled_for;
     E preserva o valor já existente.
  5. Só C mexe no ScheduledPost dentro da própria transição (DONE/posted_at/error).
"""

from __future__ import annotations

from datetime import timedelta

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
from apps.social.tasks import (
    _mark_factory_posting_still_scheduled,
    _mark_factory_posting_verified,
    _sync_factory_posting_schedule,
)


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

    def test_second_call_creates_a_DUPLICATE_log(self):
        """DIVERGÊNCIA 1 — este ramo NÃO deduplica, ao contrário de A, C, D e E.

        Duas sincronizações do mesmo post geram duas linhas em PostedVideoLog. O teste
        fixa o comportamento atual; a decisão de corrigir é do R-07.
        """
        _factory, _brand, item, post, _schedule = self.build_chain(
            platforms=["TIKTOK"], external_ids={"TIKTOK": "tt-999"}
        )

        _sync_factory_posting_schedule(post)
        _sync_factory_posting_schedule(post)

        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 2)

    def test_without_external_id_creates_log_with_EMPTY_video_id(self):
        """DIVERGÊNCIA 1 (parte 2) — sem guard de id vazio, grava log com id em branco."""
        _factory, _brand, item, post, _schedule = self.build_chain(
            platforms=["TIKTOK"], external_ids={}
        )

        _sync_factory_posting_schedule(post)

        log = PostedVideoLog.objects.get(inventory_item=item)
        self.assertEqual(log.external_video_id, "")


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
    """Cópia C — _mark_factory_posting_verified (tasks.py:823)."""

    def test_drives_post_to_done_and_marks_everything(self):
        _factory, _brand, item, post, schedule = self.build_chain(
            post_status="PENDING", retry_count=2
        )
        post.error = "erro anterior que deve ser limpo"
        post.save(update_fields=["error"])

        _mark_factory_posting_verified(
            post, platform="YT", external_video_id="vid-abc", metadata={"checked": True}
        )

        post.refresh_from_db()
        schedule.refresh_from_db()
        item.refresh_from_db()

        # DIVERGÊNCIA 5 — só esta cópia dirige o ScheduledPost para DONE.
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

        _mark_factory_posting_verified(post, platform="YT", external_video_id="vid-abc")

        post.refresh_from_db()
        self.assertEqual(post.posted_at, posted_at_original)

    def test_is_idempotent_on_second_call(self):
        _factory, _brand, item, post, _schedule = self.build_chain(post_status="PENDING")

        _mark_factory_posting_verified(post, platform="YT", external_video_id="vid-abc")
        _mark_factory_posting_verified(post, platform="YT", external_video_id="vid-abc")

        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 1)

    def test_creates_log_with_EMPTY_id_when_video_id_is_blank(self):
        """DIVERGÊNCIA 1 (parte 3) — C deduplica mas não valida id vazio, como A faz."""
        _factory, _brand, item, post, _schedule = self.build_chain(post_status="PENDING")

        _mark_factory_posting_verified(post, platform="YT", external_video_id="")

        log = PostedVideoLog.objects.get(inventory_item=item)
        self.assertEqual(log.external_video_id, "")

    def test_returns_silently_when_post_has_no_schedule(self):
        """Comportamento esquisito preservado (tasks.py:831): sem schedule, o post NÃO
        vira DONE — a transição inteira é abandonada em silêncio."""
        post = ScheduledPost.objects.create(
            scheduled_at=timezone.now(), platforms=["YTB"], status="PENDING"
        )

        _mark_factory_posting_verified(post, platform="YT", external_video_id="vid-abc")

        post.refresh_from_db()
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(PostedVideoLog.objects.count(), 0)

    def test_transition_is_not_atomic_today(self):
        """D-03 — as 4 escritas não estão em transaction.atomic().

        Fixa a ausência de atomicidade: se a criação do PostedVideoLog falhar, as três
        escritas anteriores já foram persistidas. R-06 deve INVERTER esta asserção.
        """
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")

        with self.assertRaises(RuntimeError):
            with self.settings():
                original_create = PostedVideoLog.objects.create

                def explode(*args, **kwargs):
                    raise RuntimeError("falha simulada no ultimo passo")

                PostedVideoLog.objects.create = explode
                try:
                    _mark_factory_posting_verified(
                        post, platform="YT", external_video_id="vid-abc"
                    )
                finally:
                    PostedVideoLog.objects.create = original_create

        post.refresh_from_db()
        schedule.refresh_from_db()
        item.refresh_from_db()

        # Estado parcial persistido — é exatamente a inconsistência que R-06 elimina.
        self.assertEqual(post.status, "DONE")
        self.assertEqual(schedule.status, "DONE")
        self.assertEqual(item.status, "POSTED")
        self.assertEqual(PostedVideoLog.objects.count(), 0)


class MarkFactoryPostingStillScheduledTests(PostingStateFixtureMixin, TestCase):
    """_mark_factory_posting_still_scheduled (tasks.py:870) — 2 modelos, sem atomic."""

    def test_keeps_item_scheduled_with_next_check(self):
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")

        _mark_factory_posting_still_scheduled(post, publish_at_raw=None, note="")

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

        _mark_factory_posting_still_scheduled(
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

    def test_does_NOT_clear_schedule_next_retry_at(self):
        """DIVERGÊNCIA 2 — A, C e E zeram next_retry_at; esta cópia não."""
        _factory, _brand, item, _post, schedule = self.build_chain(post_status="PENDING")
        next_retry_antes = schedule.next_retry_at
        self.assertIsNotNone(next_retry_antes)

        self.client.post(f"/api/video-inventory/{item.id}/mark-posted/", {}, format="json")

        schedule.refresh_from_db()
        self.assertEqual(schedule.next_retry_at, next_retry_antes)

    def test_does_NOT_touch_attempt_count_nor_scheduled_for(self):
        """DIVERGÊNCIA 3 e 4 — não atualiza attempt_count nem scheduled_for do item."""
        _factory, _brand, item, _post, schedule = self.build_chain(
            post_status="PENDING", retry_count=4
        )
        item.attempt_count = 0
        item.scheduled_for = None
        item.save(update_fields=["attempt_count", "scheduled_for"])

        self.client.post(f"/api/video-inventory/{item.id}/mark-posted/", {}, format="json")

        item.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(item.attempt_count, 0)
        self.assertIsNone(item.scheduled_for)
        self.assertEqual(schedule.attempt_count, 0)

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

    def test_does_NOT_touch_attempt_count(self):
        """DIVERGÊNCIA 3 — nem o schedule nem o item têm attempt_count atualizado."""
        _factory, _brand, item, _post, schedule = self.build_chain(
            post_status="DONE", external_ids={"YT": "yt-777"}, retry_count=3
        )

        call_command("fix_youtube_posted_status")

        item.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(item.attempt_count, 0)
        self.assertEqual(schedule.attempt_count, 0)

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


class PostedTransitionDivergenceTests(PostingStateFixtureMixin, TestCase):
    """Compara as cópias lado a lado — é este teste que R-07 vai usar como referência."""

    def test_all_five_copies_agree_on_the_core_three_fields(self):
        """O único ponto de acordo total: schedule DONE + item POSTED + last_error limpo.

        Tudo além destes três campos diverge entre as cópias — ver as DIVERGÊNCIAS 1-5
        no docstring do módulo e nos testes acima.
        """
        # A — YouTube
        _f, _b, item_a, post_a, sched_a = self.build_chain(platforms=["YTB"])
        _sync_factory_posting_schedule(post_a)

        # B — não-YouTube
        _f, _b, item_b, post_b, sched_b = self.build_chain(
            platforms=["TIKTOK"], external_ids={"TIKTOK": "tt-1"}
        )
        _sync_factory_posting_schedule(post_b)

        # C — verificação
        _f, _b, item_c, post_c, sched_c = self.build_chain(post_status="PENDING")
        _mark_factory_posting_verified(post_c, platform="YT", external_video_id="vid-c")

        # E — management command
        _f, _b, item_e, _post_e, sched_e = self.build_chain(
            post_status="DONE", external_ids={"YT": "yt-e"}
        )
        call_command("fix_youtube_posted_status")

        for item, schedule in (
            (item_a, sched_a),
            (item_b, sched_b),
            (item_c, sched_c),
            (item_e, sched_e),
        ):
            item.refresh_from_db()
            schedule.refresh_from_db()
            self.assertEqual(schedule.status, "DONE")
            self.assertEqual(item.status, "POSTED")
            self.assertEqual(item.last_error, "")
