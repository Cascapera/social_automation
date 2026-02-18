from django.db import models
from apps.mediahub.models import SourceVideo

class Cut(models.Model):
    source = models.ForeignKey(SourceVideo, on_delete=models.CASCADE, related_name="cuts")
    name = models.CharField(max_length=200, default="", blank=True)

    # HH:MM:SS ou HH:MM:SS.ms
    start_tc = models.CharField(max_length=16)
    end_tc = models.CharField(max_length=16)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Cut {self.id} ({self.start_tc}-{self.end_tc})"
