from django.db import models


class Brand(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    youtube_made_for_kids = models.BooleanField(
        default=False,
        help_text="Se verdadeiro, vídeos da marca são marcados como conteúdo infantil no YouTube.",
    )
    youtube_description_extra = models.TextField(
        blank=True,
        default="",
        help_text="Texto adicional padrão da descrição para uploads no YouTube.",
    )

    def __str__(self) -> str:
        return self.name

class BrandAsset(models.Model):
    ASSET_TYPES = [
        ("LOGO", "Logo"),
        ("FRAME", "Frame/Moldura"),
        ("INTRO", "Intro vídeo"),
        ("OUTRO", "Outro vídeo"),
        ("CTA", "CTA vídeo/imagem"),
        ("ANIMATION", "Animação overlay (PNG/GIF com transparência)"),
    ]

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(max_length=16, choices=ASSET_TYPES)
    file = models.FileField(upload_to="brands/assets/")
    label = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        unique_together = ("brand", "asset_type", "label")

    def __str__(self) -> str:
        return f"{self.brand.slug}:{self.asset_type}:{self.label or self.file.name}"


class BrandSocialAccount(models.Model):
    """Conta de rede social conectada a uma marca (OAuth)."""
    PLATFORM = [
        ("IG", "Instagram"),
        ("TT", "TikTok"),
        ("YT", "YouTube Shorts"),
        ("YTB", "YouTube"),
    ]

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="social_accounts")
    platform = models.CharField(max_length=4, choices=PLATFORM)
    channel_id = models.CharField(max_length=64, blank=True, default="")
    account_name = models.CharField(max_length=120, blank=True, default="")
    access_token = models.TextField(blank=True, default="")
    refresh_token = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("brand", "platform", "channel_id")
        verbose_name = "Conta social"
        verbose_name_plural = "Contas sociais"

    def __str__(self) -> str:
        name = self.account_name or self.channel_id or "?"
        return f"{self.brand.slug} / {self.get_platform_display()} / {name}"

