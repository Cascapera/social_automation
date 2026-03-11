"""
Corrige itens que já foram enviados ao YouTube (post DONE com external_ids) mas ainda
estão em "Aguardando Postagem" (item SCHEDULED/POSTING). Marca como POSTED para
aparecerem em "Vídeos Postados".

Uso (uma vez, após deploy da correção):
  python manage.py fix_youtube_posted_status
  # ou com Docker:
  docker compose exec web python manage.py fix_youtube_posted_status
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.jobs.models import (
    FactoryPostingSchedule,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)


class Command(BaseCommand):
    help = (
        "Marca como POSTED os itens cujo post já está DONE no YouTube mas o item ainda está aguardando."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        # Schedules com post DONE (YouTube já recebeu) e item ainda não POSTED
        schedules = (
            FactoryPostingSchedule.objects.filter(
                scheduled_post__status="DONE",
                inventory_item__status__in=["SCHEDULED", "POSTING"],
            )
            .select_related("scheduled_post", "inventory_item", "factory", "brand")
            .order_by("id")
        )
        updated = 0
        for schedule in schedules:
            post = schedule.scheduled_post
            item = schedule.inventory_item
            if not (post.external_ids and any(str(k).strip().upper() in ("YT", "YTB") for k in (post.external_ids or {}))):
                continue
            ext = post.external_ids or {}
            external_video_id = str(ext.get("YT") or ext.get("YTB") or "")
            platform = "YT" if ext.get("YT") else "YTB"
            if not external_video_id:
                continue
            schedule.status = "DONE"
            schedule.next_retry_at = None
            schedule.save(update_fields=["status", "next_retry_at", "updated_at"])
            item.status = "POSTED"
            item.posted_at = item.posted_at or post.posted_at or now
            item.scheduled_for = item.scheduled_for or post.scheduled_at
            item.last_error = ""
            item.save(update_fields=["status", "posted_at", "scheduled_for", "last_error", "updated_at"])
            if not PostedVideoLog.objects.filter(
                inventory_item=item,
                external_platform=platform,
                external_video_id=external_video_id,
            ).exists():
                PostedVideoLog.objects.create(
                    factory=schedule.factory,
                    brand=schedule.brand,
                    inventory_item=item,
                    external_platform=platform,
                    external_video_id=external_video_id,
                    posted_at=post.posted_at or now,
                    metadata_snapshot={
                        "scheduled_post_id": post.id,
                        "platforms": post.platforms or [],
                        "external_ids": post.external_ids or {},
                    },
                )
            updated += 1
            self.stdout.write(f"  item_id={item.id} brand={schedule.brand_id} → POSTED")
        self.stdout.write(self.style.SUCCESS(f"Atualizados {updated} itens para POSTED."))
