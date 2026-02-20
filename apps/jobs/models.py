from django.db import models
from django.conf import settings
from apps.cuts.models import Cut
from apps.brands.models import Brand, BrandAsset


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

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="scheduled_posts")
    platforms = models.JSONField(
        default=list,
        help_text="Lista de códigos: IG, TT, YT, YTB",
    )
    scheduled_at = models.DateTimeField(help_text="Data/hora para publicar")
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING")
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Job {self.job_id} → {self.scheduled_at} ({self.status})"
