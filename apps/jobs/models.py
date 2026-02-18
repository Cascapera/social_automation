from django.db import models
from apps.cuts.models import Cut
from apps.brands.models import BrandAsset


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

    def __str__(self) -> str:
        platforms = ",".join(self.target_platforms) if self.target_platforms else "-"
        return f"Job {self.id} ({platforms}) - {self.status}"

class RenderOutput(models.Model):
    job = models.OneToOneField(Job, on_delete=models.CASCADE, related_name="output")
    file = models.FileField(upload_to="exports/")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Output job={self.job.id}"
