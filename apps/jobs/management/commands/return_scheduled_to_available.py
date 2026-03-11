"""
Cancela agendamentos pendentes e devolve os vídeos para "Disponível".
Remove ScheduledPost e FactoryPostingSchedule, mas mantém o VideoInventoryItem no banco.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.jobs.models import FactoryPostingSchedule, FactoryScheduleRun, ScheduledPost, VideoInventoryItem


class Command(BaseCommand):
    help = (
        "Cancela agendamentos (SCHEDULED/POSTING) e devolve os vídeos para status AVAILABLE. "
        "Remove os agendamentos mas mantém os vídeos no banco."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--factory-id",
            type=int,
            default=None,
            help="Filtra por factory_id.",
        )
        parser.add_argument(
            "--brand-id",
            type=int,
            default=None,
            help="Filtra por brand_id.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente simula, sem alterar nada.",
        )

    def handle(self, *args, **options):
        qs = VideoInventoryItem.objects.filter(
            status__in=["SCHEDULED", "POSTING"]
        ).select_related("brand", "factory")
        if options.get("factory_id"):
            qs = qs.filter(factory_id=options["factory_id"])
        if options.get("brand_id"):
            qs = qs.filter(brand_id=options["brand_id"])

        items = list(qs.order_by("id"))
        if not items:
            self.stdout.write("Nenhum vídeo agendado encontrado com os filtros informados.")
            return

        schedules = list(
            FactoryPostingSchedule.objects.filter(
                inventory_item_id__in=[i.id for i in items]
            ).select_related("scheduled_post")
        )
        scheduled_post_ids = [
            s.scheduled_post_id for s in schedules if s.scheduled_post_id
        ]

        self.stdout.write(
            self.style.WARNING(
                f"Encontrados {len(items)} vídeo(s) agendados para devolver a AVAILABLE "
                f"(schedules={len(schedules)}, scheduled_posts={len(scheduled_post_ids)})."
            )
        )
        for item in items[:20]:
            self.stdout.write(
                f"  - id={item.id} brand={item.brand.name} type={item.video_type} "
                f"title={item.title or '-'}"
            )
        if len(items) > 20:
            self.stdout.write(f"  ... e mais {len(items) - 20}.")

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry-run finalizado. Nada foi alterado."))
            return

        from datetime import timedelta
        from django.utils import timezone
        from zoneinfo import ZoneInfo

        factories_to_reset = {i.factory_id for i in items if i.factory_id}

        with transaction.atomic():
            if scheduled_post_ids:
                ScheduledPost.objects.filter(id__in=scheduled_post_ids).delete()
            if schedules:
                FactoryPostingSchedule.objects.filter(
                    id__in=[s.id for s in schedules]
                ).delete()
            VideoInventoryItem.objects.filter(
                id__in=[i.id for i in items]
            ).update(
                status="AVAILABLE",
                scheduled_for=None,
                last_error="",
            )
            # Remove FactoryScheduleRun do dia seguinte para o cron das 19h poder rodar de novo
            for fid in factories_to_reset:
                factory = next((i.factory for i in items if i.factory_id == fid), None)
                if factory:
                    tz = ZoneInfo(factory.timezone or "America/Sao_Paulo")
                    now_local = timezone.now().astimezone(tz)
                    target_date = now_local.date() + timedelta(days=1)
                    FactoryScheduleRun.objects.filter(
                        factory_id=fid, run_date=target_date
                    ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Concluído: {len(items)} vídeo(s) devolvidos para Disponível. "
                f"FactoryScheduleRun do dia seguinte removido para permitir novo agendamento às 19h."
            )
        )
