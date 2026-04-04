"""Remove arquivos órfãos em storage/media (sem registro no banco)."""
from django.core.management.base import BaseCommand

from apps.social.tasks import _cleanup_orphan_media_files


class Command(BaseCommand):
    help = (
        "Remove arquivos físicos em storage/media que não têm registro no banco "
        "(thumbnails, vídeos de cortes, sources, exports, etc.)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista os arquivos órfãos sem deletar.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        result = _cleanup_orphan_media_files(dry_run=dry_run)
        orphans = result.get("orphans_found", [])
        deleted = result.get("orphans_deleted", 0)
        errors = result.get("errors", [])

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry-run: {len(orphans)} arquivo(s) órfão(s) encontrado(s).")
            )
            for p in orphans[:50]:
                self.stdout.write(f"  - {p}")
            if len(orphans) > 50:
                self.stdout.write(f"  ... e mais {len(orphans) - 50}")
            self.stdout.write(self.style.SUCCESS("Nada foi removido."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Removidos {deleted} arquivo(s) órfão(s).")
            )

        for err in errors:
            self.stderr.write(self.style.ERROR(str(err)))
