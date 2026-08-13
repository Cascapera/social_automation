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

from apps.jobs.models import FactoryPostingSchedule
from apps.social.services.posting_state import mark_posted


class Command(BaseCommand):
    help = (
        "Marca como POSTED os itens cujo post já está DONE no YouTube mas o item ainda está aguardando."
    )

    def handle(self, *args, **options):
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
            # Cópia E do D-02: este comando reescrevia a transição inteira à mão. Agora
            # delega ao dono único (R-07). A regra de preservar posted_at/scheduled_for
            # já existentes, que nasceu aqui, virou canônica para todos os caminhos.
            if not mark_posted(post, platform=platform, external_video_id=external_video_id):
                continue
            updated += 1
            self.stdout.write(f"  item_id={item.id} brand={schedule.brand_id} -> POSTED")
        self.stdout.write(self.style.SUCCESS(f"Atualizados {updated} itens para POSTED."))
