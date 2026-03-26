"""
Test Whisper transcription OUTSIDE Celery to diagnose GPU crashes.

Usage:
  python manage.py test_whisper_gpu storage/media/cortes_processo/1/chunk_001.wav
  python manage.py test_whisper_gpu path/to/file.wav --device cuda
  python manage.py test_whisper_gpu path/to/file.wav --device cpu

Debug with synchronous CUDA (clearer stack trace on errors):
  set CUDA_LAUNCH_BLOCKING=1
  python manage.py test_whisper_gpu file.wav --device cuda

Capture stack trace while stuck (in another terminal):
  py-spy dump --pid <PROCESS_PID>
"""
import logging
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Test Whisper transcription outside Celery (GPU crash diagnostics)"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Path to audio/video (wav, m4a, mp4, etc.)")
        parser.add_argument(
            "--device",
            choices=["cuda", "cpu"],
            default=None,
            help="Force device (default: WHISPER_DEVICE from .env or cuda)",
        )
        parser.add_argument(
            "--model",
            default="small",
            help="Model: tiny, base, small, medium, large-v3 (default: small)",
        )

    def handle(self, *args, **options):
        # Ensure Whisper logs are visible
        logging.getLogger("apps.jobs.services.subtitles").setLevel(logging.INFO)
        if not logging.getLogger().handlers:
            logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

        path = Path(options["path"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"File does not exist: {path}"))
            return

        device = options["device"]
        model_size = options["model"]

        self.stdout.write("=" * 60)
        self.stdout.write("WHISPER TEST (outside Celery)")
        self.stdout.write("=" * 60)
        self.stdout.write(f"File: {path}")
        self.stdout.write(f"Model: {model_size}")
        self.stdout.write(f"Device: {device or 'auto (env or cuda)'}")
        self.stdout.write("")

        from apps.jobs.services.subtitles import generate_subtitles

        try:
            self.stdout.write("[1/2] Loading model and starting transcription...")
            self.stdout.flush()
            segments = generate_subtitles(
                path,
                language="pt",
                model_size=model_size,
                device=device,
            )
            self.stdout.write(self.style.SUCCESS(f"[2/2] OK! {len(segments)} segments transcribed."))
            for i, seg in enumerate(segments[:5]):
                self.stdout.write(f"  {i+1}. [{seg.get('start', 0):.1f}s] {seg.get('text', '')[:60]}...")
            if len(segments) > 5:
                self.stdout.write(f"  ... and {len(segments) - 5} more segments")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"ERROR: {e}"))
            import traceback
            traceback.print_exc()
            raise
