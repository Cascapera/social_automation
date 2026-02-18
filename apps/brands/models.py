from django.db import models


class Brand(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)

    def __str__(self) -> str:
        return self.name

class BrandAsset(models.Model):
    ASSET_TYPES = [
        ("LOGO", "Logo"),
        ("FRAME", "Frame/Moldura"),
        ("INTRO", "Intro vídeo"),
        ("OUTRO", "Outro vídeo"),
        ("CTA", "CTA vídeo/imagem"),
    ]

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(max_length=16, choices=ASSET_TYPES)
    file = models.FileField(upload_to="brands/assets/")
    label = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        unique_together = ("brand", "asset_type", "label")

    def __str__(self) -> str:
        return f"{self.brand.slug}:{self.asset_type}:{self.label or self.file.name}"

