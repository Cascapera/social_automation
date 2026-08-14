"""Cota esgotada em todas as credenciais do YouTube (refactor.md R-10 / D-01).

Este teste existe por um motivo específico: o R-10 encontrou, dentro do laço de publicação
nativa, **um bloco inalcançável** — ~15 linhas depois de um `continue` incondicional,
tratando exatamente este caso. Ele nunca executou.

Antes de apagar código, é preciso provar que o comportamento dele não some junto. É isso
que este arquivo faz: mostra que a guarda **viva** (a de `available_credentials`, que roda
antes) já cobre o caso — o post é adiado com erro reagendável e razão `quotaExceeded`.

A mensagem das duas versões era diferente ("todas as credenciais YouTube da brand estão sem
cota" na viva, "cota excedida em todas as credenciais da brand" na morta). A que o usuário
sempre viu é a da guarda viva; a outra nunca chegou a lugar nenhum.

Só dá para escrever este teste porque o R-10 tirou o laço da função de 1.216 linhas —
`publish_native_platforms` agora é chamável direto.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.brands.models import Brand, BrandSocialAccount, BrandYouTubeCredential, Factory
from apps.jobs.models import ScheduledPost
from apps.social.services.publishing.youtube_native import publish_native_platforms


class CotaEsgotadaTests(TestCase):
    def setUp(self):
        super().setUp()
        factory = Factory.objects.create(name="Factory R10")
        self.brand = Brand.objects.create(name="Brand R10", slug="brand-r10", factory=factory)
        self.account = BrandSocialAccount.objects.create(
            brand=self.brand, platform="YT", channel_id="UC-canal-r10"
        )
        self.post = ScheduledPost.objects.create(
            job=None,
            social_account=self.account,
            platforms=["YT"],
            scheduled_at=timezone.now(),
            title="Vídeo do teste",
            status="POSTING",
        )

    def credencial(self, *, sem_cota_ate=None, ordem=0):
        return BrandYouTubeCredential.objects.create(
            brand=self.brand,
            is_active=True,
            order_index=ordem,
            refresh_token="refresh-token-de-teste",
            quota_exceeded_until=sem_cota_ate,
        )

    def publicar(self):
        return publish_native_platforms(
            self.post,
            self.brand,
            video_path="/tmp/nao-usado.mp4",
            job=None,
            correlation_id="corr-r10",
            brand_id=self.brand.id,
            upload_fingerprint="",
            upload_post_youtube_ok=False,
            external_ids={},
            upload_post_client_request_id_key="upload_post_client_request_id",
        )

    def test_todas_sem_cota_adia_em_vez_de_falhar(self):
        """O post não pode ir para `FAILED`: a cota volta sozinha, o vídeo ainda serve."""
        futuro = timezone.now() + timedelta(hours=2)
        self.credencial(sem_cota_ate=futuro, ordem=0)
        self.credencial(sem_cota_ate=futuro + timedelta(hours=1), ordem=1)

        resultado = self.publicar()

        self.assertEqual(resultado.errors, [], "cota esgotada não é erro definitivo")
        self.assertEqual(len(resultado.retryable_errors), 1)
        erro = resultado.retryable_errors[0]
        self.assertEqual(erro["reason"], "quotaExceeded")
        self.assertIn("sem cota", erro["message"])

    def test_o_adiamento_espera_a_credencial_que_libera_primeiro(self):
        """Esperar pela mais distante desperdiçaria a janela da que já voltou."""
        perto = timezone.now() + timedelta(minutes=30)
        longe = timezone.now() + timedelta(hours=6)
        self.credencial(sem_cota_ate=longe, ordem=0)
        self.credencial(sem_cota_ate=perto, ordem=1)

        resultado = self.publicar()

        espera = resultado.retryable_errors[0]["retry_after_seconds"]
        self.assertGreater(espera, 60)
        self.assertLessEqual(espera, 30 * 60 + 5)

    def test_piso_de_60_segundos_no_adiamento(self):
        """Cota que vence em segundos não pode virar retry imediato em loop."""
        self.credencial(sem_cota_ate=timezone.now() + timedelta(seconds=1), ordem=0)

        resultado = self.publicar()

        self.assertGreaterEqual(resultado.retryable_errors[0]["retry_after_seconds"], 60)

    def test_credencial_com_cota_vencida_volta_a_ser_usada(self):
        """Contraprova: `quota_exceeded_until` no passado não conta como esgotada.

        Sem isso, uma credencial ficaria bloqueada para sempre depois do primeiro estouro.

        O publisher é dublê: sem ele, o teste tentaria falar com o OAuth do Google de
        verdade — e um teste que depende de rede é um teste que vai falhar por outro
        motivo, um dia, sem avisar o que quebrou.
        """
        credencial = self.credencial(sem_cota_ate=timezone.now() - timedelta(hours=1), ordem=0)
        usados = []

        class PublisherDuble:
            def publish(self, account, video_path, job, scheduled_post=None, youtube_credential=None):
                usados.append(youtube_credential.id if youtube_credential else None)
                return {"video_id": "vid-r10"}

        with patch("apps.social.publishers.get_publisher", return_value=PublisherDuble()):
            resultado = self.publicar()

        self.assertEqual(usados, [credencial.id], "a credencial liberada devia ter sido usada")
        self.assertEqual(
            [e for e in resultado.retryable_errors if e.get("reason") == "quotaExceeded"],
            [],
            "credencial com cota já liberada não devia adiar o post",
        )
        self.assertEqual(resultado.errors, [])
