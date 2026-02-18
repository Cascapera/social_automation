from django.db import models
from apps.brands.models import Brand

class SourceVideo(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="source_videos")
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="sources/")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.brand.slug} - {self.title}"
