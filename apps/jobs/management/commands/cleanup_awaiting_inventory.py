"""Remove videos from the bank in 'Awaiting posting' state."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.jobs.models import FactoryPostingSchedule, ScheduledPost, VideoInventoryItem


class Command(BaseCommand):
    help = (
        "Remove videos awaiting posting from inventory, "
        "deleting local media and related records."
    )

    DEFAULT_STATUSES = ["AVAILABLE", "SCHEDULED", "FAILED"]

    def add_arguments(self, parser):
        parser.add_argument(
            "--factory-id",
            type=int,
            default=None,
            help="Filter by factory_id.",
        )
        parser.add_argument(
            "--brand-id",
            action="append",
            type=int,
            default=None,
            help="Filter by brand_id (repeatable).",
        )
        parser.add_argument(
            "--video-type",
            choices=["SHORT", "LONG"],
            default=None,
            help="Filter by video type.",
        )
        parser.add_argument(
            "--status",
            action="append",
            default=None,
            help=(
                "Target status. Repeatable (e.g. --status AVAILABLE --status SCHEDULED). "
                "Default: AVAILABLE,SCHEDULED,FAILED"
            ),
        )
        parser.add_argument(
            "--include-posting",
            action="store_true",
            help="Include POSTING status (use with care).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate only; delete nothing.",
        )

    def handle(self, *args, **options):
        statuses = [s.strip().upper() for s in (options.get("status") or self.DEFAULT_STATUSES) if s]
        if options["include_posting"] and "POSTING" not in statuses:
            statuses.append("POSTING")

        valid_statuses = {"AVAILABLE", "SCHEDULED", "POSTING", "FAILED"}
        invalid = [s for s in statuses if s not in valid_statuses]
        if invalid:
            raise CommandError(f"Invalid status(es): {', '.join(invalid)}")

        qs = VideoInventoryItem.objects.select_related("auto_cut_corte").filter(status__in=statuses)
        if options.get("factory_id"):
            qs = qs.filter(factory_id=options["factory_id"])
        if options.get("brand_id"):
            qs = qs.filter(brand_id__in=options["brand_id"])
        if options.get("video_type"):
            qs = qs.filter(video_type=options["video_type"])

        items = list(qs.order_by("id"))
        if not items:
            self.stdout.write("No videos awaiting posting found with the given filters.")
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
                f"Found {len(items)} item(s) to clean "
                f"(schedules={len(schedule_ids)}, scheduled_posts={len(scheduled_post_ids)})."
            )
        )
        self.stdout.write(
            f"Filters => statuses={statuses}, factory_id={options.get('factory_id')}, "
            f"brand_ids={options.get('brand_id')}, video_type={options.get('video_type')}"
        )

        for item in items[:30]:
            self.stdout.write(
                f"- inventory_id={item.id} brand_id={item.brand_id} "
                f"type={item.video_type} status={item.status} title={item.title or '-'}"
            )
        if len(items) > 30:
            self.stdout.write(f"... and {len(items) - 30} more item(s).")

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry-run finished. Nothing was removed."))
            return

        deleted_media_files = 0
        deleted_media_thumbs = 0
        with transaction.atomic():
            # 1) ScheduledPost linked to inventory (via FactoryPostingSchedule)
            if scheduled_post_ids:
                ScheduledPost.objects.filter(id__in=scheduled_post_ids).delete()

            # 2) Remaining FactoryPostingSchedule
            if schedule_ids:
                FactoryPostingSchedule.objects.filter(id__in=schedule_ids).delete()

            # 3) Physical files for finalized cuts
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
                            f"Failed to delete main file for cut={corte.id} (inventory={item.id})."
                        )
                if getattr(corte, "thumbnail", None):
                    try:
                        corte.thumbnail.delete(save=False)
                        deleted_media_thumbs += 1
                    except Exception:
                        self.stderr.write(
                            f"Failed to delete thumbnail for cut={corte.id} (inventory={item.id})."
                        )

            # 4) Remove inventory rows
            VideoInventoryItem.objects.filter(id__in=inventory_ids).delete()

        self.stdout.write(
            self.style.SUCCESS(
                "Cleanup completed: "
                f"inventory={len(inventory_ids)}, schedules={len(schedule_ids)}, "
                f"scheduled_posts={len(scheduled_post_ids)}, files={deleted_media_files}, "
                f"thumbnails={deleted_media_thumbs}."
            )
        )
