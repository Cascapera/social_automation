from django.db import models
from django.conf import settings
from apps.brands.models import Brand

class SourceVideo(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="source_videos",
        null=True,
        blank=True,
    )
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="source_videos")
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="sources/")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.brand.slug} - {self.title}"
