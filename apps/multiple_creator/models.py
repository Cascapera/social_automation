from django.conf import settings
from django.db import models

from apps.brands.models import Brand
from apps.mediahub.models import SourceVideo


class MultipleCreatorJob(models.Model):
    """Submit unico que dispara analise de cortes para varias brands em paralelo.

    O pipeline real (transcricao unica + fanout por brand) chega nas Fases 5-6.
    Esta Fase 4 entrega so a persistencia: criar o job e suas BrandExecution.
    """

    STATUS = [
        ("PENDING_TRANSCRIPTION", "Pendente - transcricao"),
        ("TRANSCRIBING", "Transcrevendo"),
        ("READY", "Pronto para fanout"),
        ("RUNNING_BRANDS", "Processando brands"),
        ("DONE", "Concluido"),
        ("PARTIAL", "Concluido com falhas parciais"),
        ("ERROR", "Erro"),
    ]
    SOURCE_KIND = [
        ("FILE", "Arquivo enviado"),
        ("SOURCE", "SourceVideo existente"),
        ("YOUTUBE", "URL YouTube"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="multiple_creator_jobs",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=200, blank=True, default="")

    source_kind = models.CharField(max_length=10, choices=SOURCE_KIND)
    source = models.ForeignKey(
        SourceVideo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="multiple_creator_jobs",
    )
    file = models.FileField(
        upload_to="multiple_creator/sources/",
        max_length=255,
        null=True,
        blank=True,
    )
    youtube_url = models.URLField(max_length=500, blank=True, default="")

    assunto = models.CharField(max_length=300, blank=True, default="")
    convidados = models.CharField(max_length=300, blank=True, default="")
    prompt_version = models.CharField(max_length=64, blank=True, default="educational")
    vertical_mode = models.CharField(max_length=32, blank=True, default="zoom_crop")
    shorts_target = models.PositiveSmallIntegerField(default=12)
    longs_target = models.PositiveSmallIntegerField(default=3)
    thumbnail_font = models.CharField(max_length=64, blank=True, default="")
    thumbnail_band_color = models.CharField(max_length=7, blank=True, default="")
    thumbnail_text_color = models.CharField(max_length=7, blank=True, default="")
    thumbnail_stroke_color = models.CharField(max_length=7, blank=True, default="")

    transcript = models.TextField(blank=True, default="")
    transcript_segments = models.JSONField(null=True, blank=True)

    status = models.CharField(max_length=30, choices=STATUS, default="PENDING_TRANSCRIPTION")
    progress = models.PositiveSmallIntegerField(default=0)
    progress_message = models.CharField(max_length=255, blank=True, default="")
    error = models.TextField(blank=True, default="")
    correlation_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        label = self.name or f"Job #{self.pk}"
        return f"{label} ({self.status})"


class MultipleCreatorBrandExecution(models.Model):
    """Execucao individual por brand vinculada a um MultipleCreatorJob.

    Cada execucao referencia (na Fase 6) a AutoCutAnalysis filha correspondente.
    Aqui (Fase 4) ela e criada apenas como PENDING.
    """

    STATUS = [
        ("PENDING", "Pendente"),
        ("ANALYZING", "Analisando"),
        ("FINALIZING", "Finalizando"),
        ("DONE", "Concluido"),
        ("ERROR", "Erro"),
    ]

    job = models.ForeignKey(
        MultipleCreatorJob,
        on_delete=models.CASCADE,
        related_name="brand_executions",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.PROTECT,
        related_name="multiple_creator_executions",
    )
    auto_cut_analysis = models.ForeignKey(
        "auto_cuts.AutoCutAnalysis",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="multiple_creator_execution",
    )

    status = models.CharField(max_length=20, choices=STATUS, default="PENDING")
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "brand"], name="uniq_brand_per_mc_job"),
        ]
        ordering = ["job_id", "id"]

    def __str__(self):
        return f"Job #{self.job_id} brand={self.brand_id} ({self.status})"
