"""Remove pastas temporárias de jobs em storage/media/jobs/."""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Remove pastas temporárias em storage/media/jobs/ (arquivos já exportados para exports/)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas lista o que seria removido, sem deletar",
        )

    def handle(self, *args, **options):
        jobs_dir = Path(settings.MEDIA_ROOT) / "jobs"
        if not jobs_dir.exists():
            self.stdout.write("Pasta jobs/ não existe.")
            return

        removed = 0
        for item in jobs_dir.iterdir():
            if item.is_dir():
                if options["dry_run"]:
                    self.stdout.write(f"[dry-run] Removeria: {item}")
                else:
                    try:
                        shutil.rmtree(item)
                        self.stdout.write(f"Removido: {item}")
                        removed += 1
                    except OSError as e:
                        self.stderr.write(f"Erro ao remover {item}: {e}")

        self.stdout.write(self.style.SUCCESS(f"Concluído. {removed} pasta(s) removida(s)."))
