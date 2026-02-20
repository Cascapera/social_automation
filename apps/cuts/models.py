from django.db import models
from django.conf import settings
from apps.brands.models import Brand
from apps.mediahub.models import SourceVideo


class Cut(models.Model):
    FORMAT_CHOICES = [
        ("vertical", "Vertical (9:16)"),
        ("horizontal", "Horizontal (16:9)"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cuts",
        null=True,
        blank=True,
    )
    source = models.ForeignKey(
        SourceVideo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuts",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cuts",
        help_text="Marca para cortes de upload (cortes de source herdam da source)",
    )
    name = models.CharField(max_length=200, default="", blank=True)
    start_tc = models.CharField(max_length=16)
    end_tc = models.CharField(max_length=16)
    format = models.CharField(
        max_length=16,
        choices=FORMAT_CHOICES,
        default="vertical",
    )
    duration = models.FloatField(
        null=True,
        blank=True,
        help_text="Duração em segundos",
    )
    file = models.FileField(upload_to="cuts/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Cut {self.id} ({self.start_tc}-{self.end_tc})"
