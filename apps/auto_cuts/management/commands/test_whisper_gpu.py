"""
Testa transcrição Whisper FORA do Celery para diagnosticar crash na GPU.

Uso:
  python manage.py test_whisper_gpu storage/media/cortes_processo/1/chunk_001.m4a
  python manage.py test_whisper_gpu caminho/arquivo.m4a --device cuda
  python manage.py test_whisper_gpu caminho/arquivo.m4a --device cpu

Para debug com CUDA síncrono (melhor stack trace em erros):
  set CUDA_LAUNCH_BLOCKING=1
  python manage.py test_whisper_gpu arquivo.m4a --device cuda

Para capturar stack trace quando travar (em outro terminal):
  py-spy dump --pid <PID_DO_PROCESSO>
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
    help = "Testa transcrição Whisper fora do Celery (diagnóstico de crash GPU)"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Caminho do áudio/vídeo (m4a, mp4, etc.)")
        parser.add_argument(
            "--device",
            choices=["cuda", "cpu"],
            default=None,
            help="Força device (default: usa WHISPER_DEVICE do .env ou cuda)",
        )
        parser.add_argument(
            "--model",
            default="small",
            help="Modelo: tiny, base, small, medium, large-v3 (default: small)",
        )

    def handle(self, *args, **options):
        # Garante que logs do Whisper apareçam
        logging.getLogger("apps.jobs.services.subtitles").setLevel(logging.INFO)
        if not logging.getLogger().handlers:
            logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

        path = Path(options["path"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"Arquivo não existe: {path}"))
            return

        device = options["device"]
        model_size = options["model"]

        self.stdout.write("=" * 60)
        self.stdout.write("TESTE WHISPER (fora do Celery)")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Arquivo: {path}")
        self.stdout.write(f"Modelo: {model_size}")
        self.stdout.write(f"Device: {device or 'auto (env ou cuda)'}")
        self.stdout.write("")

        from apps.jobs.services.subtitles import generate_subtitles

        try:
            self.stdout.write("[1/2] Carregando modelo e iniciando transcrição...")
            self.stdout.flush()
            segments = generate_subtitles(
                path,
                language="pt",
                model_size=model_size,
                device=device,
            )
            self.stdout.write(self.style.SUCCESS(f"[2/2] OK! {len(segments)} segmentos transcritos."))
            for i, seg in enumerate(segments[:5]):
                self.stdout.write(f"  {i+1}. [{seg.get('start', 0):.1f}s] {seg.get('text', '')[:60]}...")
            if len(segments) > 5:
                self.stdout.write(f"  ... e mais {len(segments) - 5} segmentos")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"ERRO: {e}"))
            import traceback
            traceback.print_exc()
            raise
