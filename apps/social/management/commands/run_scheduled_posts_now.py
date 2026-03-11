"""
Comando para testar o fluxo de agendamento/publicação sem esperar o Celery Beat.

Uso:
  # Dispara a checagem de posts pendentes agora (como se o Beat tivesse rodado)
  python manage.py run_scheduled_posts_now

  # Enfileira apenas a task de checagem (worker processa)
  python manage.py run_scheduled_posts_now --queue

  # Tenta publicar um ScheduledPost específico (para teste isolado)
  python manage.py run_scheduled_posts_now --post-id 123
"""
from django.core.management.base import BaseCommand

from apps.jobs.models import ScheduledPost


class Command(BaseCommand):
    help = (
        "Dispara a checagem de posts agendados agora (para teste) ou publica um post específico."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--queue",
            action="store_true",
            help="Enfileira check_scheduled_posts_task em vez de rodar no processo.",
        )
        parser.add_argument(
            "--post-id",
            type=int,
            default=None,
            help="ID do ScheduledPost para tentar publicar apenas esse (enfileira post_to_platforms_task).",
        )

    def handle(self, *args, **options):
        use_queue = options["queue"]
        post_id = options["post_id"]

        if post_id is not None:
            try:
                post = ScheduledPost.objects.get(id=post_id)
            except ScheduledPost.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"ScheduledPost id={post_id} não existe."))
                return
            if post.status != "PENDING":
                self.stdout.write(
                    self.style.WARNING(
                        f"Post {post_id} está com status={post.status}. "
                        "Só posts PENDING são publicados. Enfileirando mesmo assim para teste."
                    )
                )
            from apps.social.tasks import post_to_platforms_task

            if use_queue:
                post_to_platforms_task.delay(post_id)
                self.stdout.write(self.style.SUCCESS(f"Enfileirado post_to_platforms_task(post_id={post_id})."))
            else:
                result = post_to_platforms_task(post_id)
                self.stdout.write(self.style.SUCCESS(f"Resultado: {result}"))
            return

        from apps.social.tasks import check_scheduled_posts_task

        if use_queue:
            check_scheduled_posts_task.delay()
            self.stdout.write(
                self.style.SUCCESS("Enfileirado check_scheduled_posts_task. O worker vai processar.")
            )
        else:
            result = check_scheduled_posts_task()
            self.stdout.write(self.style.SUCCESS(f"Checagem rodou: {result}"))
