"""Regressão: `update_fields` de ScheduledPost referenciando campo inexistente.

`ScheduledPost` (apps/jobs/models.py:341-412) **não tem** campo `updated_at` — ao
contrário de `FactoryPostingSchedule` e `VideoInventoryItem`, que têm. Dois pontos em
`apps/social/tasks.py` incluíam `"updated_at"` no `update_fields` de um `ScheduledPost`,
o que faz o Django levantar:

    ValueError: The following fields do not exist in this model, are m2m fields,
    primary keys, or are non-concrete fields: updated_at

Não era erro silencioso: derrubava o fluxo inteiro nos dois casos.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from apps.brands.models import Brand, BrandSocialAccount, Factory
from apps.jobs.models import (
    FactoryPostingSchedule,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.social.services.posting_state import mark_posted
from apps.social.tasks import post_youtube_first_comment_task


class ScheduledPostHasNoUpdatedAtTests(TestCase):
    def test_model_really_has_no_updated_at(self):
        """Fixa a premissa dos demais testes deste arquivo.

        Se um dia `updated_at` for adicionado ao modelo (com migration), este teste falha
        e avisa que os `update_fields` podem voltar a incluí-lo.
        """
        campos = {f.name for f in ScheduledPost._meta.get_fields()}
        self.assertNotIn("updated_at", campos)
        self.assertIn("created_at", campos)
        self.assertIn("posted_at", campos)


class MarkFactoryPostingVerifiedPersistsTests(TestCase):
    """Regressão do site 1: apps/social/tasks.py:838.

    `mark_posted` é o passo que confirma no banco que um vídeo foi
    publicado. É chamado pelas duas tasks de reconciliação do YouTube
    (`reconcile_youtube_schedules_task:2187` e `reconcile_youtube_full_scan_task:2430`),
    ambas dentro de um `try/except Exception` amplo — então o ValueError não aparecia
    como erro de campo, e sim como "a reconciliação falhou", abortando a rodada inteira
    já no primeiro vídeo confirmado.
    """

    def setUp(self):
        self.factory = Factory.objects.create(name="Factory Regressao")
        self.brand = Brand.objects.create(
            name="Brand Regressao", slug="brand-regressao", factory=self.factory
        )
        self.item = VideoInventoryItem.objects.create(
            factory=self.factory,
            brand=self.brand,
            video_type="SHORT",
            status="SCHEDULED",
            title="Video de regressao",
        )
        self.post = ScheduledPost.objects.create(
            scheduled_at=timezone.now() - timedelta(minutes=5),
            platforms=["YTB"],
            status="PENDING",
            external_ids={"YTB": "vid-regressao"},
        )
        self.schedule = FactoryPostingSchedule.objects.create(
            factory=self.factory,
            brand=self.brand,
            inventory_item=self.item,
            video_type="SHORT",
            scheduled_at=self.post.scheduled_at,
            status="PLANNED",
            scheduled_post=self.post,
        )

    def test_persists_the_whole_transition_without_raising(self):
        mark_posted(
            self.post, platform="YTB", external_video_id="vid-regressao"
        )

        self.post.refresh_from_db()
        self.schedule.refresh_from_db()
        self.item.refresh_from_db()

        self.assertEqual(self.post.status, "DONE")
        self.assertIsNotNone(self.post.posted_at)
        self.assertEqual(self.schedule.status, "DONE")
        self.assertEqual(self.item.status, "POSTED")
        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=self.item).count(), 1)


class FirstCommentFlagPersistsTests(TestCase):
    """Regressão do site 2: apps/social/tasks.py:3837.

    A docstring da task promete "Idempotente: se external_ids['first_comment_posted'] já
    está True, sai". O flag nunca era gravado, então com `acks_late=True` e
    `max_retries=2` o comentário fixado podia ser postado mais de uma vez no mesmo vídeo.
    """

    def setUp(self):
        self.factory = Factory.objects.create(name="Factory Comentario")
        self.brand = Brand.objects.create(
            name="Brand Comentario", slug="brand-comentario", factory=self.factory
        )
        self.account = BrandSocialAccount.objects.create(
            brand=self.brand, platform="YTB", channel_id="canal-1"
        )
        self.post = ScheduledPost.objects.create(
            scheduled_at=timezone.now(),
            platforms=["YTB"],
            status="DONE",
            social_account=self.account,
            external_ids={"YTB": "vid-comentario"},
        )

    def _run_task(self):
        with (
            patch("apps.social.services.youtube_credentials.get_credentials", return_value=MagicMock()),
            patch("googleapiclient.discovery.build", return_value=MagicMock()),
            patch(
                "apps.social.publishers.youtube.YouTubePublisher._post_pinned_first_comment"
            ) as post_comment,
        ):
            resultado = post_youtube_first_comment_task(self.post.id, "vid-comentario")
        return resultado, post_comment

    def test_flag_is_persisted_after_posting(self):
        resultado, post_comment = self._run_task()

        self.assertTrue(resultado["ok"])
        post_comment.assert_called_once()

        self.post.refresh_from_db()
        self.assertTrue(self.post.external_ids.get("first_comment_posted"))

    def test_second_run_skips_without_posting_again(self):
        """É a idempotência que o bug quebrava: sem o flag gravado, o retry repostava."""
        self._run_task()

        resultado, post_comment = self._run_task()

        self.assertEqual(resultado["skipped"], "already_posted")
        post_comment.assert_not_called()
