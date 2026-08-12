"""Testes do serviço `apps/social/services/posting_state.py` (refactor.md R-06 / D-03).

O R-06 não mudou nenhum campo, nenhum `update_fields` e nenhum `PostedVideoLog`: o estado
final dos 4 modelos é bit a bit o mesmo de antes. Isso continua travado pelos 31
characterization tests do R-03, que passam sem alteração de asserção.

**A única diferença observável é atomicidade** — e é isso que este arquivo cobre. O R-03
prova o estado final; este prova o que acontece quando a transição falha no meio.

Por que importa: a transição escreve em 4 tabelas em sequência. Sem transação, uma falha
no meio deixava `ScheduledPost` em `DONE` com o `VideoInventoryItem` ainda em `SCHEDULED`
— a inconsistência que obrigou a existir o comando de reparo
`apps/social/management/commands/fix_youtube_posted_status.py`.

Os testes abortam num passo **intermediário**, não no último. Abortar no último passo
prova pouco: as escritas anteriores é que precisam voltar atrás.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.jobs.models import (
    FactoryPostingSchedule,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.social.services import posting_state
from apps.social.services.posting_state import mark_posted, mark_still_scheduled
from apps.social.tests.test_posting_state_characterization import PostingStateFixtureMixin


class MarkPostedAtomicityTests(PostingStateFixtureMixin, TestCase):
    """`mark_posted` escreve nos 4 modelos ou em nenhum."""

    def test_falha_no_meio_nao_persiste_nenhuma_das_escritas_anteriores(self):
        """Aborta em `item.save()` — o 3º passo — e nada dos 2 primeiros sobrevive.

        A ordem das escritas é post → schedule → item → PostedVideoLog. Falhando no
        terceiro, o post e o schedule já tinham sido salvos no modelo antigo.
        """
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")
        estado_inicial = (post.status, schedule.status, item.status)

        with patch.object(
            VideoInventoryItem, "save", side_effect=RuntimeError("falha no 3o passo")
        ):
            with self.assertRaises(RuntimeError):
                mark_posted(post, platform="YT", external_video_id="vid-abc")

        post.refresh_from_db()
        schedule.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual((post.status, schedule.status, item.status), estado_inicial)
        self.assertEqual(PostedVideoLog.objects.count(), 0)

    def test_falha_no_segundo_passo_preserva_o_scheduled_post(self):
        """Aborta em `schedule.save()` — o 2º passo — e o `ScheduledPost` volta atrás.

        Este é o caso mais perigoso do modelo antigo: o post ia para DONE e saía da lista
        de espera, mas o slot da factory continuava PLANNED. O item nunca era publicado e
        ninguém era avisado.
        """
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")

        with patch.object(
            FactoryPostingSchedule, "save", side_effect=RuntimeError("falha no 2o passo")
        ):
            with self.assertRaises(RuntimeError):
                mark_posted(post, platform="YT", external_video_id="vid-abc")

        post.refresh_from_db()
        self.assertEqual(post.status, "PENDING")
        self.assertIsNone(post.posted_at)

    def test_sem_factory_schedule_e_no_op(self):
        """Publicação avulsa não tem estado de factory para fechar — sai sem escrever."""
        post = ScheduledPost.objects.create(
            scheduled_at=timezone.now(),
            platforms=["YTB"],
            status="PENDING",
            external_ids={},
        )

        mark_posted(post, platform="YTB", external_video_id="vid-avulso")

        post.refresh_from_db()
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(PostedVideoLog.objects.count(), 0)


class MarkStillScheduledAtomicityTests(PostingStateFixtureMixin, TestCase):
    """`mark_still_scheduled` escreve nos 2 modelos ou em nenhum."""

    def test_falha_no_item_nao_persiste_o_schedule(self):
        """Aborta em `item.save()` e o `next_retry_at` do schedule não fica gravado.

        Sem a transação, o schedule ficaria com um horário de reconferência agendado
        enquanto o item continuava com o estado antigo — os dois discordando sobre o que
        acontece a seguir.
        """
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")
        next_retry_inicial = schedule.next_retry_at
        status_inicial = schedule.status

        with patch.object(VideoInventoryItem, "save", side_effect=RuntimeError("falha")):
            with self.assertRaises(RuntimeError):
                mark_still_scheduled(post, publish_at_raw=None, note="nota")

        schedule.refresh_from_db()
        self.assertEqual(schedule.next_retry_at, next_retry_inicial)
        self.assertEqual(schedule.status, status_inicial)

    def test_sem_factory_schedule_e_no_op(self):
        post = ScheduledPost.objects.create(
            scheduled_at=timezone.now(),
            platforms=["YTB"],
            status="PENDING",
            external_ids={},
        )

        mark_still_scheduled(post, publish_at_raw=None, note="nota")

        post.refresh_from_db()
        self.assertEqual(post.status, "PENDING")


class PostingStateModuleContractTests(TestCase):
    """Anti-drift: guarda a regra que o módulo declara no próprio docstring."""

    def test_o_servico_nao_faz_io_de_rede(self):
        """Nenhuma chamada de rede pode entrar nos blocos `transaction.atomic()`.

        A transição segura lock em até 4 tabelas. Uma chamada HTTP lá dentro transforma
        alguns milissegundos de lock em segundos, e sob carga isso vira contenção no
        Postgres. Este teste falha se alguém importar um cliente HTTP neste módulo — que
        é o primeiro passo para o erro acontecer.

        Se precisar de dado externo, busque **antes** de entrar na transição e passe por
        parâmetro (é o que `mark_posted` já faz com `metadata`).
        """
        importados = set(vars(posting_state))
        for proibido in ("requests", "httpx", "urllib", "urlopen", "socket"):
            self.assertNotIn(
                proibido,
                importados,
                f"{proibido} importado em posting_state.py — nenhum I/O de rede pode "
                "acontecer dentro das transições; busque o dado antes do atomic()",
            )
