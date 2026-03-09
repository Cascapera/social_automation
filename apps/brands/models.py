from django.db import models


class Factory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    timezone = models.CharField(
        max_length=64,
        default="America/Sao_Paulo",
        help_text="Timezone padrão da factory (IANA). Ex: America/Sao_Paulo.",
    )
    is_active = models.BooleanField(default=True)
    scheduling_paused = models.BooleanField(
        default=False,
        help_text="Quando ativo, pausa apenas o agendamento/publicação da factory.",
    )
    processing_paused = models.BooleanField(
        default=False,
        help_text="Quando ativo, impede início de novos jobs de cortes automáticos na factory.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Factory"
        verbose_name_plural = "Factories"

    def __str__(self) -> str:
        return self.name


class Brand(models.Model):
    THEME_CATEGORY_CHOICES = [
        ("BUSINESS_MONEY", "Negócios / Dinheiro"),
        ("PSYCHOLOGY_RELATIONSHIPS", "Psicologia / Relacionamentos"),
        ("STORIES_CURIOSITIES", "Histórias e Curiosidades"),
        ("CONTROVERSIES_DEBATE", "Polêmicas / Debate"),
        ("COMEDY_HUMOR", "Comédia / Humor"),
    ]
    THUMBNAIL_FONT_CHOICES = [
        ("anton", "Anton"),
        ("bebas", "Bebas Neue"),
        ("montserrat", "Montserrat ExtraBold"),
        ("impact", "Impact"),
    ]

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    factory = models.ForeignKey(
        Factory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="brands",
    )
    theme_category = models.CharField(
        max_length=40,
        choices=THEME_CATEGORY_CHOICES,
        blank=True,
        default="",
        help_text="Categoria principal da brand dentro da factory (1:1 por factory).",
    )
    youtube_made_for_kids = models.BooleanField(
        default=False,
        help_text="Se verdadeiro, vídeos da marca são marcados como conteúdo infantil no YouTube.",
    )
    youtube_description_extra = models.TextField(
        blank=True,
        default="",
        help_text="Texto adicional padrão da descrição para uploads no YouTube.",
    )
    youtube_client_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Client ID OAuth do Google para esta brand/canal (opcional; fallback no .env).",
    )
    youtube_client_secret = models.TextField(
        blank=True,
        default="",
        help_text="Client Secret OAuth do Google para esta brand/canal (opcional; fallback no .env).",
    )
    youtube_redirect_uri = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Redirect URI OAuth para esta brand/canal (opcional; fallback no .env).",
    )
    thumbnail_font = models.CharField(
        max_length=20,
        choices=THUMBNAIL_FONT_CHOICES,
        default="impact",
        help_text="Fonte padrão para thumbnails da brand.",
    )
    thumbnail_band_color = models.CharField(
        max_length=7,
        default="#E12E20",
        help_text="Cor da faixa da thumbnail (#RRGGBB).",
    )
    thumbnail_text_color = models.CharField(
        max_length=7,
        default="#0A0A0A",
        help_text="Cor principal do texto da thumbnail (#RRGGBB).",
    )
    thumbnail_effect_color = models.CharField(
        max_length=7,
        default="#FFEBDC",
        help_text="Cor do contorno/efeito do texto da thumbnail (#RRGGBB).",
    )
    min_short_interval_minutes = models.PositiveIntegerField(
        default=60,
        help_text="Intervalo mínimo entre shorts (min).",
    )
    min_long_interval_minutes = models.PositiveIntegerField(
        default=180,
        help_text="Intervalo mínimo entre vídeos longos (min).",
    )
    max_shorts_per_day = models.PositiveIntegerField(
        default=3,
        help_text="Máximo de shorts por dia.",
    )
    max_longs_per_day = models.PositiveIntegerField(
        default=1,
        help_text="Máximo de vídeos longos por dia.",
    )
    short_window_start = models.TimeField(
        null=True,
        blank=True,
        help_text="Início da janela de postagem de shorts.",
    )
    short_window_end = models.TimeField(
        null=True,
        blank=True,
        help_text="Fim da janela de postagem de shorts.",
    )
    long_window_start = models.TimeField(
        null=True,
        blank=True,
        help_text="Início da janela de postagem de longos.",
    )
    long_window_end = models.TimeField(
        null=True,
        blank=True,
        help_text="Fim da janela de postagem de longos.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["factory", "theme_category"],
                condition=~models.Q(theme_category=""),
                name="uniq_theme_category_per_factory",
            )
        ]

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


class BrandYouTubeCredential(models.Model):
    """Credenciais OAuth do YouTube por brand, com fallback em ordem."""

    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="youtube_credentials",
    )
    label = models.CharField(max_length=120, blank=True, default="")
    order_index = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    is_for_check = models.BooleanField(
        default=False,
        help_text="Se True, usada apenas para reconciliação/check do YouTube (YOUTUBE_CHECK_*).",
    )

    client_id = models.CharField(max_length=255, blank=True, default="")
    client_secret = models.TextField(blank=True, default="")
    redirect_uri = models.CharField(max_length=500, blank=True, default="")

    channel_id = models.CharField(max_length=64, blank=True, default="")
    account_name = models.CharField(max_length=120, blank=True, default="")
    access_token = models.TextField(blank=True, default="")
    refresh_token = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)

    quota_exceeded_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order_index", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["brand", "order_index"],
                name="uniq_brand_youtube_credential_order",
            )
        ]
        verbose_name = "Credencial YouTube da brand"
        verbose_name_plural = "Credenciais YouTube da brand"

    def __str__(self) -> str:
        base = self.label or f"Credencial #{self.id}"
        channel = self.account_name or self.channel_id or "sem canal"
        return f"{self.brand.slug} / {base} / {channel}"

