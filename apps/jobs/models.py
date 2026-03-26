from django.conf import settings
from django.db import models

from apps.auto_cuts.models import AutoCutCorte
from apps.brands.models import Brand, BrandAsset, BrandSocialAccount, Factory
from apps.cuts.models import Cut


class JobCut(models.Model):
    """Corte incluído no job. Ordem = ordem de adição (id). Para mudar: remover e adicionar de novo."""
    job = models.ForeignKey("Job", on_delete=models.CASCADE, related_name="job_cuts")
    cut = models.ForeignKey(Cut, on_delete=models.CASCADE, related_name="job_cuts")
    order = models.PositiveSmallIntegerField(default=0)  # mantido para compatibilidade; ordenação = id

    class Meta:
        ordering = ["id"]
        verbose_name = "Corte do job"
        verbose_name_plural = "Cortes do job"

    def __str__(self) -> str:
        return f"Job {self.job_id} - Cut {self.cut_id}"


class Job(models.Model):
    STATUS = [
        ("QUEUED", "Queued"),
        ("RUNNING", "Running"),
        ("DONE", "Done"),
        ("FAILED", "Failed"),
    ]

    PLATFORM = [
        ("IG", "Instagram Reels"),
        ("TT", "TikTok"),
        ("YT", "YouTube Shorts"),
        ("YTB", "YouTube"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="jobs",
        null=True,
        blank=True,
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="jobs",
        help_text="Marca para filtrar e isolar jobs",
    )
    name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Nome para identificar o job no dashboard",
    )
    cuts = models.ManyToManyField(
        Cut,
        through=JobCut,
        related_name="jobs",
        blank=True,
        help_text="Cortes na ordem: intro → corte1 → corte2 → ... → outro",
    )
    def _default_platforms():
        return ["YT"]

    target_platforms = models.JSONField(
        default=_default_platforms,
        help_text="Redes onde o vídeo será postado automaticamente. Marque as que deseja usar.",
    )

    intro_asset = models.ForeignKey(
        BrandAsset, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    outro_asset = models.ForeignKey(
        BrandAsset, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    make_vertical = models.BooleanField(default=True)

    TRANSITION = [
        ("none", "Nenhuma"),
        ("fade", "Fade (crossfade)"),
        ("fadeblack", "Fade por preto"),
        ("wipeleft", "Wipe esquerda"),
        ("wiperight", "Wipe direita"),
        ("dissolve", "Dissolve"),
    ]
    transition = models.CharField(
        max_length=16, choices=TRANSITION, default="none"
    )
    transition_duration = models.DecimalField(
        max_digits=3, decimal_places=1, default=0.5,
        help_text="Duração em segundos (ex: 0.5, 1.0, 0.2)",
    )

    status = models.CharField(max_length=10, choices=STATUS, default="QUEUED")
    progress = models.PositiveSmallIntegerField(default=0)  # 0..100
    log = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    archived = models.BooleanField(default=False)

    # Legendas (Whisper)
    subtitle_status = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="generating, ready_for_edit, burning, burned, error",
    )
    subtitle_segments = models.JSONField(
        null=True,
        blank=True,
        help_text="[{start, end, text}, ...]",
    )
    subtitle_style = models.JSONField(
        null=True,
        blank=True,
        help_text="{font, size, color, outline_color, position}",
    )
    subtitle_error = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        label = self.name or f"Job {self.id}"
        platforms = ",".join(self.target_platforms) if self.target_platforms else "-"
        return f"{label} ({platforms}) - {self.status}"

class RenderOutput(models.Model):
    job = models.OneToOneField(Job, on_delete=models.CASCADE, related_name="output")
    file = models.FileField(upload_to="exports/")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Output job={self.job.id}"


class ScheduledPost(models.Model):
    """Agendamento de postagem do vídeo final em redes sociais."""
    STATUS = [
        ("PENDING", "Pendente"),
        ("POSTING", "Postando"),
        ("DONE", "Concluído"),
        ("FAILED", "Falhou"),
    ]

    job = models.ForeignKey(
        Job,
        on_delete=models.SET_NULL,
        related_name="scheduled_posts",
        null=True,
        blank=True,
        help_text="Job de origem. SET_NULL ao deletar job para preservar histórico de agendamento.",
    )
    auto_cut_corte = models.ForeignKey(
        AutoCutCorte,
        on_delete=models.SET_NULL,
        related_name="scheduled_posts",
        null=True,
        blank=True,
        help_text="Corte finalizado do Auto Cuts (opcional). SET_NULL ao deletar para preservar histórico.",
    )
    platforms = models.JSONField(
        default=list,
        help_text="Lista de códigos: IG, TT, YT, YTB",
    )
    social_account = models.ForeignKey(
        BrandSocialAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_posts",
        help_text="Conta a usar (opcional; se vazio, usa primeira conta da marca para a plataforma)",
    )
    scheduled_at = models.DateTimeField(help_text="Data/hora para publicar")
    title = models.CharField(max_length=200, blank=True, default="")
    description = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, help_text="Lista de tags/keywords")
    privacy_status = models.CharField(
        max_length=12,
        choices=[("public", "Público"), ("private", "Privado"), ("unlisted", "Não listado")],
        default="private",
    )
    upload_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 do arquivo enviado para deduplicação por canal/plataforma.",
    )
    external_ids = models.JSONField(
        default=dict,
        help_text="IDs externos por plataforma (ex.: {'YT': 'abc123'}).",
    )
    retry_count = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING")
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        if self.job_id:
            label = f"Job {self.job_id}"
        elif self.auto_cut_corte_id:
            label = f"AutoCutCorte {self.auto_cut_corte_id}"
        else:
            label = "Sem origem"
        return f"{label} → {self.scheduled_at} ({self.status})"


class VideoInventoryItem(models.Model):
    STATUS = [
        ("AVAILABLE", "Disponível"),
        ("SCHEDULED", "Agendado"),
        ("POSTING", "Postando"),
        ("POSTED", "Postado"),
        ("FAILED", "Falhou"),
    ]
    VIDEO_TYPE = [
        ("SHORT", "Short"),
        ("LONG", "Long"),
    ]

    factory = models.ForeignKey(
        Factory,
        on_delete=models.CASCADE,
        related_name="video_inventory_items",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="video_inventory_items",
    )
    auto_cut_corte = models.OneToOneField(
        AutoCutCorte,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_item",
        help_text="Corte de origem. SET_NULL ao deletar para preservar histórico de agendamento.",
    )
    video_type = models.CharField(max_length=8, choices=VIDEO_TYPE)
    title = models.CharField(max_length=220, blank=True, default="")
    description = models.TextField(blank=True, default="")
    virality_score = models.PositiveSmallIntegerField(null=True, blank=True)
    source_asset_id = models.CharField(max_length=120, blank=True, default="")
    source_metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="AVAILABLE")
    scheduled_for = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-virality_score", "id"]

    def __str__(self) -> str:
        return f"{self.brand} {self.video_type} ({self.status})"


class FactoryScheduleRun(models.Model):
    """Controle idempotente da geração diária de agenda por factory."""

    factory = models.ForeignKey(
        Factory,
        on_delete=models.CASCADE,
        related_name="schedule_runs",
    )
    run_date = models.DateField(help_text="Data local da factory em que a geração ocorreu.")
    timezone = models.CharField(max_length=64, default="America/Sao_Paulo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("factory", "run_date")
        ordering = ["-run_date", "-id"]

    def __str__(self) -> str:
        return f"{self.factory} {self.run_date}"


class FactoryPostingSchedule(models.Model):
    STATUS = [
        ("PLANNED", "Planejado"),
        ("POSTING", "Postando"),
        ("DONE", "Concluído"),
        ("FAILED", "Falhou"),
        ("SKIPPED", "Ignorado"),
    ]
    VIDEO_TYPE = [
        ("SHORT", "Short"),
        ("LONG", "Long"),
    ]

    factory = models.ForeignKey(
        Factory,
        on_delete=models.CASCADE,
        related_name="posting_schedules",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="posting_schedules",
    )
    inventory_item = models.ForeignKey(
        VideoInventoryItem,
        on_delete=models.CASCADE,
        related_name="posting_schedules",
    )
    video_type = models.CharField(max_length=8, choices=VIDEO_TYPE)
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATUS, default="PLANNED")
    attempt_count = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    scheduled_post = models.OneToOneField(
        ScheduledPost,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="factory_schedule",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_at", "id"]

    def __str__(self) -> str:
        return f"{self.brand} {self.video_type} {self.scheduled_at}"


class FactoryPostingAttemptLog(models.Model):
    RESULT = [
        ("SUCCESS", "Sucesso"),
        ("ERROR", "Erro"),
    ]

    posting_schedule = models.ForeignKey(
        FactoryPostingSchedule,
        on_delete=models.CASCADE,
        related_name="attempt_logs",
    )
    attempt_number = models.PositiveSmallIntegerField(default=1)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    result = models.CharField(max_length=10, choices=RESULT)
    error_code = models.CharField(max_length=120, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    provider_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"Attempt {self.attempt_number} ({self.result})"


class PostedVideoLog(models.Model):
    factory = models.ForeignKey(
        Factory,
        on_delete=models.CASCADE,
        related_name="posted_video_logs",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="posted_video_logs",
    )
    inventory_item = models.ForeignKey(
        VideoInventoryItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_logs",
    )
    external_platform = models.CharField(max_length=8, default="YT")
    external_video_id = models.CharField(max_length=120, blank=True, default="")
    posted_at = models.DateTimeField()
    metadata_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-posted_at", "-id"]

    def __str__(self) -> str:
        return f"{self.brand} {self.external_platform} {self.posted_at}"
