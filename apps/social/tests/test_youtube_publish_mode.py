"""Tests for YouTubePublisher._resolve_publish_mode (random direct-public).

`ImmediatePrepublishTests` cobre o envio antecipado do botão "Postar Imediato": ele fica
fora do sorteio de `public` direto, senão 30% dos vídeos longos iriam ao ar no momento do
upload em vez do horário do slot.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.social.publishers.youtube import YouTubePublisher


def _make_post_with_future_schedule():
    post = MagicMock()
    post.scheduled_at = timezone.now() + timedelta(hours=2)
    return post


class ResolvePublishModeTests(SimpleTestCase):
    def test_shorts_always_use_publish_at(self):
        publisher = YouTubePublisher()
        post = _make_post_with_future_schedule()
        account = MagicMock()
        account.platform = "YT"
        with patch("apps.social.publishers.youtube.random.random", return_value=0.0):
            publish_at, privacy = publisher._resolve_publish_mode(post, account)
        self.assertIsNotNone(publish_at)
        self.assertIsNone(privacy)

    def test_long_goes_direct_public_when_rng_below_threshold(self):
        publisher = YouTubePublisher()
        post = _make_post_with_future_schedule()
        account = MagicMock()
        account.platform = "YTB"
        with patch("apps.social.publishers.youtube.random.random", return_value=0.1):
            publish_at, privacy = publisher._resolve_publish_mode(post, account)
        self.assertIsNone(publish_at)
        self.assertEqual(privacy, "public")

    def test_long_uses_publish_at_when_rng_above_threshold(self):
        publisher = YouTubePublisher()
        post = _make_post_with_future_schedule()
        account = MagicMock()
        account.platform = "YTB"
        with patch("apps.social.publishers.youtube.random.random", return_value=0.99):
            publish_at, privacy = publisher._resolve_publish_mode(post, account)
        self.assertIsNotNone(publish_at)
        self.assertIsNone(privacy)

    def test_no_scheduled_at_returns_no_publish_at(self):
        publisher = YouTubePublisher()
        post = MagicMock()
        post.scheduled_at = None
        account = MagicMock()
        account.platform = "YTB"
        publish_at, privacy = publisher._resolve_publish_mode(post, account)
        self.assertIsNone(publish_at)
        self.assertIsNone(privacy)


def _make_post(*, scheduled_at, privacy_status="private", external_ids=None):
    post = MagicMock()
    post.scheduled_at = scheduled_at
    post.privacy_status = privacy_status
    post.external_ids = external_ids if external_ids is not None else {}
    return post


def _make_account(platform):
    account = MagicMock()
    account.platform = platform
    return account


class ImmediatePrepublishTests(SimpleTestCase):
    """Upload antecipado: o vídeo sobe agora, mas só vai ao ar no horário do slot."""

    def test_long_marcado_nao_entra_no_sorteio_de_public_direto(self):
        publisher = YouTubePublisher()
        post = _make_post(
            scheduled_at=timezone.now() + timedelta(days=1),
            external_ids={"immediate_prepublish": True},
        )
        with patch("apps.social.publishers.youtube.random.random", return_value=0.0):
            publish_at, privacy = publisher._resolve_publish_mode(post, _make_account("YTB"))
        # Sem o marcador, este rng devolveria (None, "public") e o vídeo iria ao ar agora.
        self.assertIsNotNone(publish_at)
        self.assertIsNone(privacy)

    def test_external_ids_nao_dict_nao_quebra_o_sorteio(self):
        """`post.external_ids` pode vir `None` (post antigo) — e mock não é dicionário."""
        publisher = YouTubePublisher()
        post = _make_post(scheduled_at=timezone.now() + timedelta(days=1))
        post.external_ids = None
        with patch("apps.social.publishers.youtube.random.random", return_value=0.0):
            publish_at, privacy = publisher._resolve_publish_mode(post, _make_account("YTB"))
        self.assertIsNone(publish_at)
        self.assertEqual(privacy, "public")


class ResolvePublishAtAndPrivacyTests(SimpleTestCase):
    """A privacidade final do `videos.insert` — o que decide vídeo visível ou não."""

    def test_com_publish_at_o_video_sobe_privado(self):
        publisher = YouTubePublisher()
        post = _make_post(scheduled_at=timezone.now() + timedelta(hours=2), privacy_status="public")
        with patch("apps.social.publishers.youtube.random.random", return_value=0.99):
            publish_at, privacy = publisher._resolve_publish_at_and_privacy(
                post, _make_account("YTB")
            )
        self.assertIsNotNone(publish_at)
        self.assertEqual(privacy, "private")

    def test_envio_antecipado_sem_publish_at_vira_public(self):
        """O slot ficou a menos de 30s enquanto o post esperava na fila.

        Privado aqui seria vídeo invisível para sempre: nada no repositório volta para
        abri-lo depois.
        """
        publisher = YouTubePublisher()
        post = _make_post(
            scheduled_at=timezone.now() + timedelta(seconds=5),
            external_ids={"immediate_prepublish": True},
        )
        publish_at, privacy = publisher._resolve_publish_at_and_privacy(post, _make_account("YT"))
        self.assertIsNone(publish_at)
        self.assertEqual(privacy, "public")

    def test_post_normal_sem_publish_at_mantem_a_privacidade_do_post(self):
        """Sem o marcador nada muda — este é o caminho do beat, e ele não é alterado."""
        publisher = YouTubePublisher()
        post = _make_post(scheduled_at=timezone.now() - timedelta(hours=1))
        publish_at, privacy = publisher._resolve_publish_at_and_privacy(post, _make_account("YT"))
        self.assertIsNone(publish_at)
        self.assertEqual(privacy, "private")

    def test_privacidade_invalida_cai_para_private(self):
        publisher = YouTubePublisher()
        post = _make_post(scheduled_at=None, privacy_status="qualquer-coisa")
        publish_at, privacy = publisher._resolve_publish_at_and_privacy(post, _make_account("YT"))
        self.assertIsNone(publish_at)
        self.assertEqual(privacy, "private")

    def test_sem_post_cai_para_private(self):
        publisher = YouTubePublisher()
        publish_at, privacy = publisher._resolve_publish_at_and_privacy(None, _make_account("YT"))
        self.assertIsNone(publish_at)
        self.assertEqual(privacy, "private")

    def test_sorteio_de_public_direto_prevalece_quando_nao_ha_publish_at(self):
        publisher = YouTubePublisher()
        post = _make_post(scheduled_at=timezone.now() + timedelta(hours=2))
        with patch("apps.social.publishers.youtube.random.random", return_value=0.0):
            publish_at, privacy = publisher._resolve_publish_at_and_privacy(
                post, _make_account("YTB")
            )
        self.assertIsNone(publish_at)
        self.assertEqual(privacy, "public")
