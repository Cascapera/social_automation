"""Limpa vídeos do Banco em 'Aguardando Postagem'."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.jobs.models import FactoryPostingSchedule, ScheduledPost, VideoInventoryItem


class Command(BaseCommand):
    help = (
        "Remove vídeos aguardando postagem do inventário, "
        "apagando mídia local e registros relacionados."
    )

    DEFAULT_STATUSES = ["AVAILABLE", "SCHEDULED", "FAILED"]

    def add_arguments(self, parser):
        parser.add_argument(
            "--factory-id",
            type=int,
            default=None,
            help="Filtra por factory_id.",
        )
        parser.add_argument(
            "--brand-id",
            action="append",
            type=int,
            default=None,
            help="Filtra por brand_id (pode repetir).",
        )
        parser.add_argument(
            "--video-type",
            choices=["SHORT", "LONG"],
            default=None,
            help="Filtra por tipo de vídeo.",
        )
        parser.add_argument(
            "--status",
            action="append",
            default=None,
            help=(
                "Status alvo. Pode repetir (ex.: --status AVAILABLE --status SCHEDULED). "
                "Padrão: AVAILABLE,SCHEDULED,FAILED"
            ),
        )
        parser.add_argument(
            "--include-posting",
            action="store_true",
            help="Inclui status POSTING no alvo (uso com cuidado).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente simula, sem deletar nada.",
        )

    def handle(self, *args, **options):
        statuses = [s.strip().upper() for s in (options.get("status") or self.DEFAULT_STATUSES) if s]
        if options["include_posting"] and "POSTING" not in statuses:
            statuses.append("POSTING")

        valid_statuses = {"AVAILABLE", "SCHEDULED", "POSTING", "FAILED"}
        invalid = [s for s in statuses if s not in valid_statuses]
        if invalid:
            raise CommandError(f"Status inválido(s): {', '.join(invalid)}")

        qs = VideoInventoryItem.objects.select_related("auto_cut_corte").filter(status__in=statuses)
        if options.get("factory_id"):
            qs = qs.filter(factory_id=options["factory_id"])
        if options.get("brand_id"):
            qs = qs.filter(brand_id__in=options["brand_id"])
        if options.get("video_type"):
            qs = qs.filter(video_type=options["video_type"])

        items = list(qs.order_by("id"))
        if not items:
            self.stdout.write("Nenhum vídeo aguardando postagem encontrado com os filtros informados.")
            return

        inventory_ids = [item.id for item in items]
        schedule_ids = list(
            FactoryPostingSchedule.objects.filter(inventory_item_id__in=inventory_ids).values_list("id", flat=True)
        )
        scheduled_post_ids = list(
            FactoryPostingSchedule.objects.filter(id__in=schedule_ids, scheduled_post_id__isnull=False)
            .values_list("scheduled_post_id", flat=True)
        )

        self.stdout.write(
            self.style.WARNING(
                f"Encontrados {len(items)} item(ns) para limpar "
                f"(schedules={len(schedule_ids)}, scheduled_posts={len(scheduled_post_ids)})."
            )
        )
        self.stdout.write(
            f"Filtros => statuses={statuses}, factory_id={options.get('factory_id')}, "
            f"brand_ids={options.get('brand_id')}, video_type={options.get('video_type')}"
        )

        for item in items[:30]:
            self.stdout.write(
                f"- inventory_id={item.id} brand_id={item.brand_id} "
                f"type={item.video_type} status={item.status} title={item.title or '-'}"
            )
        if len(items) > 30:
            self.stdout.write(f"... e mais {len(items) - 30} item(ns).")

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry-run finalizado. Nada foi removido."))
            return

        deleted_media_files = 0
        deleted_media_thumbs = 0
        with transaction.atomic():
            # 1) ScheduledPost vinculados ao inventory (via FactoryPostingSchedule)
            if scheduled_post_ids:
                ScheduledPost.objects.filter(id__in=scheduled_post_ids).delete()

            # 2) FactoryPostingSchedule remanescentes
            if schedule_ids:
                FactoryPostingSchedule.objects.filter(id__in=schedule_ids).delete()

            # 3) Arquivos físicos de corte finalizado
            for item in items:
                corte = item.auto_cut_corte
                if not corte:
                    continue
                if getattr(corte, "file", None):
                    try:
                        corte.file.delete(save=False)
                        deleted_media_files += 1
                    except Exception:
                        self.stderr.write(
                            f"Falha ao apagar arquivo principal do corte={corte.id} (inventory={item.id})."
                        )
                if getattr(corte, "thumbnail", None):
                    try:
                        corte.thumbnail.delete(save=False)
                        deleted_media_thumbs += 1
                    except Exception:
                        self.stderr.write(
                            f"Falha ao apagar thumbnail do corte={corte.id} (inventory={item.id})."
                        )

            # 4) Remove do banco de inventário
            VideoInventoryItem.objects.filter(id__in=inventory_ids).delete()

        self.stdout.write(
            self.style.SUCCESS(
                "Limpeza concluída com sucesso: "
                f"inventory={len(inventory_ids)}, schedules={len(schedule_ids)}, "
                f"scheduled_posts={len(scheduled_post_ids)}, files={deleted_media_files}, "
                f"thumbnails={deleted_media_thumbs}."
            )
        )
