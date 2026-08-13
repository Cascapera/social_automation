"""Testes do serviço `apps/social/services/posting_state.py` (refactor.md R-06/R-07, D-03).

Divisão de trabalho entre os dois arquivos de teste da máquina de estados:
`test_posting_state_characterization.py` entra por cada um dos 5 pontos de chamada e prova
o **estado final**; este aqui bate direto no serviço e prova o que os pontos de chamada
não conseguem alcançar — o que acontece quando a transição **falha no meio**, e os casos
de borda de `mark_item_posted`.

Por que importa: a transição escreve em 4 tabelas em sequência. Sem transação, uma falha
no meio deixava `ScheduledPost` em `DONE` com o `VideoInventoryItem` ainda em `SCHEDULED`
— a inconsistência que obrigou a existir o comando de reparo
`apps/social/management/commands/fix_youtube_posted_status.py`.

Os testes abortam num passo **intermediário**, não no último. Abortar no último passo
prova pouco: as escritas anteriores é que precisam voltar atrás.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.brands.models import Brand, Factory
from apps.jobs.models import (
    FactoryPostingSchedule,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)
from apps.social.services import posting_state
from apps.social.services.posting_state import (
    mark_item_posted,
    mark_posted,
    mark_still_scheduled,
)
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


class MarkItemPostedTests(PostingStateFixtureMixin, TestCase):
    """`mark_item_posted` — a entrada ancorada no item, usada pela marcação manual (R-07).

    A cópia D do D-02 partia do `VideoInventoryItem`, não de um post, porque o operador
    aponta para o vídeo e não para a tentativa de publicação. Estes testes cobrem o que
    essa assimetria exige e que `mark_posted` não faz: item sem schedule nenhum, schedule
    sem post, e mais de um schedule no mesmo item.
    """

    def test_item_sem_schedule_ainda_vira_posted_com_log(self):
        """Marcação manual de item avulso — o caso que `mark_posted` recusa.

        `mark_posted` sai sem escrever quando não há `FactoryPostingSchedule`, porque a
        reconciliação depende disso. Pela API a regra é o oposto: o operador está
        afirmando que publicou, e um item sem agenda de factory é justamente o que mais
        costuma ser marcado à mão.
        """
        factory = Factory.objects.create(name="Factory avulsa")
        brand = Brand.objects.create(name="Brand avulsa", slug="brand-avulsa", factory=factory)
        item = VideoInventoryItem.objects.create(
            factory=factory, brand=brand, video_type="SHORT", status="AVAILABLE"
        )

        mark_item_posted(item, platform="MANUAL", external_video_id="manual")

        item.refresh_from_db()
        self.assertEqual(item.status, "POSTED")
        self.assertIsNotNone(item.posted_at)
        self.assertEqual(item.last_error, "")
        self.assertEqual(PostedVideoLog.objects.filter(inventory_item=item).count(), 1)

    def test_posted_at_explicito_vence_o_horario_ja_gravado(self):
        """Única exceção à regra 4 — e o motivo de ela existir.

        A regra geral preserva `item.posted_at`, para que um reparo nunca reescreva por
        cima de um horário real. Mas quando o operador digita a data na API, ele está
        justamente corrigindo o horário: preservar ali seria descartar a correção em
        silêncio, que é o oposto do que ele pediu.
        """
        ja_gravado = (timezone.now() - timedelta(days=9)).replace(microsecond=0)
        corrigido_para = (timezone.now() - timedelta(days=2)).replace(microsecond=0)
        _factory, _brand, item, _post, _schedule = self.build_chain(
            post_status="PENDING", item_posted_at=ja_gravado
        )

        mark_item_posted(
            item, platform="MANUAL", external_video_id="manual", posted_at=corrigido_para
        )

        item.refresh_from_db()
        self.assertEqual(item.posted_at, corrigido_para)

    def test_sem_posted_at_explicito_o_horario_gravado_e_preservado(self):
        """Regra 4 no caminho normal: sem data digitada, nada é reescrito."""
        ja_gravado = (timezone.now() - timedelta(days=9)).replace(microsecond=0)
        _factory, _brand, item, _post, _schedule = self.build_chain(
            post_status="PENDING", item_posted_at=ja_gravado
        )

        mark_item_posted(item, platform="MANUAL", external_video_id="manual")

        item.refresh_from_db()
        self.assertEqual(item.posted_at, ja_gravado)

    def test_schedule_sem_scheduled_post_e_fechado_do_mesmo_jeito(self):
        """Schedule órfão de post não pode impedir a marcação nem estourar."""
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")
        schedule.scheduled_post = None
        schedule.save(update_fields=["scheduled_post"])

        mark_item_posted(item, platform="MANUAL", external_video_id="manual")

        item.refresh_from_db()
        schedule.refresh_from_db()
        post.refresh_from_db()
        self.assertEqual(item.status, "POSTED")
        self.assertEqual(schedule.status, "DONE")
        self.assertIsNone(schedule.next_retry_at)
        # O post ficou solto: sem ligação, não é dele que a marcação está falando.
        self.assertEqual(post.status, "PENDING")

    def test_com_varios_schedules_fecha_todos_e_usa_o_maior_attempt_count(self):
        """`attempt_count` do item não pode esconder a tentativa mais alta."""
        _factory, _brand, item, post_a, schedule_a = self.build_chain(
            post_status="PENDING", retry_count=1
        )
        post_b = ScheduledPost.objects.create(
            scheduled_at=timezone.now(), platforms=["YTB"], status="PENDING", retry_count=5
        )
        schedule_b = FactoryPostingSchedule.objects.create(
            factory=schedule_a.factory,
            brand=schedule_a.brand,
            inventory_item=item,
            video_type="SHORT",
            scheduled_at=timezone.now(),
            status="PLANNED",
            scheduled_post=post_b,
        )

        mark_item_posted(item, platform="MANUAL", external_video_id="manual")

        item.refresh_from_db()
        schedule_a.refresh_from_db()
        schedule_b.refresh_from_db()
        post_a.refresh_from_db()
        post_b.refresh_from_db()

        self.assertEqual(post_a.status, "DONE")
        self.assertEqual(post_b.status, "DONE")
        self.assertEqual(schedule_a.status, "DONE")
        self.assertEqual(schedule_b.status, "DONE")
        self.assertEqual(schedule_a.attempt_count, 1)
        self.assertEqual(schedule_b.attempt_count, 5)
        self.assertEqual(item.attempt_count, 5)

    def test_falha_no_item_nao_persiste_os_posts_ja_fechados(self):
        """Atomicidade da entrada pelo item: os posts do laço voltam atrás junto."""
        _factory, _brand, item, post, schedule = self.build_chain(post_status="PENDING")

        with patch.object(VideoInventoryItem, "save", side_effect=RuntimeError("falha")):
            with self.assertRaises(RuntimeError):
                mark_item_posted(item, platform="MANUAL", external_video_id="manual")

        post.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(post.status, "PENDING")
        self.assertEqual(schedule.status, "PLANNED")
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
        parâmetro (é o que `mark_posted` já faz com `log_metadata`).
        """
        importados = set(vars(posting_state))
        for proibido in ("requests", "httpx", "urllib", "urlopen", "socket"):
            self.assertNotIn(
                proibido,
                importados,
                f"{proibido} importado em posting_state.py — nenhum I/O de rede pode "
                "acontecer dentro das transições; busque o dado antes do atomic()",
            )
