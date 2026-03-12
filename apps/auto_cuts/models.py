from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from apps.brands.models import Brand
from apps.mediahub.models import SourceVideo


class AutoCutAnalysis(models.Model):
    """Análise de vídeo para sugestão automática de cortes virais."""

    STATUS = [
        ("pending", "Pendente"),
        ("transcribing", "Transcrevendo"),
        ("analyzing", "Analisando"),
        ("done", "Concluído"),
        ("error", "Erro"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auto_cut_analyses",
        null=True,
        blank=True,
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="auto_cut_analyses",
        null=True,
        blank=True,
    )
    target_brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auto_cut_analyses_targeted",
        help_text="Quando definido, todos os cortes deste vídeo vão para este canal (ignora theme_category da IA).",
    )
    source = models.ForeignKey(
        SourceVideo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auto_cut_analyses",
        help_text="Source existente (opcional)",
    )
    file = models.FileField(
        upload_to="auto_cuts/sources/",
        max_length=255,
        null=True,
        blank=True,
        help_text="Vídeo enviado diretamente (se não usar source)",
    )
    youtube_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="URL do YouTube (alternativa ao upload/source)",
    )
    name = models.CharField(max_length=200, default="", blank=True)
    assunto = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Assunto/tema principal do vídeo",
    )
    convidados = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Nomes dos convidados separados por vírgula (ex: João, Maria, Pedro)",
    )
    prompt_version = models.CharField(
        max_length=24,
        choices=[
            ("viral", "Viral (PT)"),
            ("educational", "Educacional (PT)"),
            ("viral_en", "Viral (EN)"),
            ("educational_en", "Educacional (EN)"),
            ("viral_translate", "Viral Translate - EN to PT"),
        ],
        default="viral",
        help_text="Modelo de prompt e idioma: PT, EN ou EN→PT com legendas",
    )
    THUMBNAIL_FONT_CHOICES = [
        ("anton", "Anton"),
        ("bebas", "Bebas Neue"),
        ("montserrat", "Montserrat ExtraBold"),
        ("impact", "Impact"),
    ]
    thumbnail_font = models.CharField(
        max_length=20,
        choices=THUMBNAIL_FONT_CHOICES,
        default="impact",
        help_text="Fonte usada na thumbnail automática.",
    )
    thumbnail_band_color = models.CharField(
        max_length=7,
        default="#E12E20",
        help_text="Cor da faixa inferior da thumbnail em HEX (#RRGGBB).",
    )
    thumbnail_text_color = models.CharField(
        max_length=7,
        default="#0A0A0A",
        help_text="Cor principal do texto da thumbnail em HEX (#RRGGBB).",
    )
    thumbnail_stroke_color = models.CharField(
        max_length=7,
        default="#FFEBDC",
        help_text="Cor do contorno/efeito do texto da thumbnail em HEX (#RRGGBB).",
    )
    shorts_target = models.PositiveSmallIntegerField(
        default=12,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text="Quantidade de cortes curtos para manter (1-30).",
    )
    longs_target = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="Quantidade de cortes longos para manter (1-10).",
    )
    status = models.CharField(max_length=20, choices=STATUS, default="pending")
    progress = models.PositiveSmallIntegerField(default=0)  # 0-100
    progress_message = models.CharField(max_length=200, default="", blank=True)
    transcript = models.TextField(blank=True, default="")
    transcript_segments = models.JSONField(
        null=True,
        blank=True,
        help_text="Segmentos Whisper [{start, end, text}]",
    )
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"AutoCut #{self.id} – {self.name or 'Sem nome'} ({self.status})"

    @property
    def video_file(self):
        """Retorna o arquivo de vídeo (source ou upload direto)."""
        if self.source and self.source.file:
            return self.source.file
        return self.file


class AutoCutSuggestion(models.Model):
    """Sugestão de corte viral gerada pela análise."""

    CUT_TYPE = [
        ("short", "Curto (Reels/TikTok/Shorts)"),
        ("long", "Longo (YouTube)"),
    ]
    THEME_CATEGORY_CHOICES = [
        ("BUSINESS_MONEY", "Negócios / Dinheiro"),
        ("PSYCHOLOGY_RELATIONSHIPS", "Psicologia / Relacionamentos"),
        ("STORIES_CURIOSITIES", "Histórias e Curiosidades"),
        ("CONTROVERSIES_DEBATE", "Polêmicas / Debate"),
        ("COMEDY_HUMOR", "Comédia / Humor"),
    ]

    analysis = models.ForeignKey(
        AutoCutAnalysis,
        on_delete=models.CASCADE,
        related_name="suggestions",
    )
    cut_type = models.CharField(max_length=10, choices=CUT_TYPE)
    start_tc = models.CharField(max_length=16, help_text="Ex: 12:34 ou 01:23:45")
    end_tc = models.CharField(max_length=16)
    title = models.CharField(max_length=200, default="", blank=True)
    reason = models.TextField(blank=True, default="")
    hook = models.CharField(max_length=500, blank=True, default="")
    virality_score = models.PositiveSmallIntegerField(null=True, blank=True)  # 1-10
    theme_category = models.CharField(
        max_length=40,
        choices=THEME_CATEGORY_CHOICES,
        blank=True,
        default="",
        help_text="Categoria temática retornada pela LLM (metadado obrigatório no novo fluxo).",
    )
    source_asset_id = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="ID do vídeo original de origem (usado para diversidade de publicação).",
    )
    rank = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    duration_minutes = models.FloatField(null=True, blank=True)
    raw_data = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["cut_type", "rank", "id"]

    def __str__(self) -> str:
        return f"{self.get_cut_type_display()}: {self.title or self.start_tc}–{self.end_tc}"


class AutoCutCorte(models.Model):
    """
    Corte extraído do auto-cuts. Criado ao retorno da LLM.
    Usuário marca para finalizar e incluir legenda; ao finalizar, vira corte persistente.
    """

    FORMAT_CHOICES = [
        ("vertical", "Vertical (9:16)"),
        ("horizontal", "Horizontal (16:9)"),
    ]

    analysis = models.ForeignKey(
        AutoCutAnalysis,
        on_delete=models.CASCADE,
        related_name="cortes",
    )
    suggestion = models.ForeignKey(
        AutoCutSuggestion,
        on_delete=models.CASCADE,
        related_name="cortes",
    )
    file = models.FileField(
        upload_to="auto_cuts/cortes/",
        max_length=255,
        blank=True,
        null=True,
        help_text="Vídeo extraído (antes ou depois de queimar legenda)",
    )
    format = models.CharField(
        max_length=16,
        choices=FORMAT_CHOICES,
        default="vertical",
    )
    needs_subtitle = models.BooleanField(
        default=False,
        help_text="Usuário marcou para incluir legenda",
    )
    user_wants_finalize = models.BooleanField(
        default=False,
        help_text="Usuário marcou para finalizar este corte",
    )
    is_finalized = models.BooleanField(
        default=False,
        help_text="Corte foi finalizado (legenda queimada ou vídeo pronto)",
    )
    subtitle_segments = models.JSONField(
        null=True,
        blank=True,
        help_text="Segmentos de legenda [{start, end, text}]",
    )
    thumbnail = models.ImageField(
        upload_to="auto_cuts/thumbnails/",
        max_length=255,
        null=True,
        blank=True,
        help_text="Thumbnail opcional para publicação no YouTube.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["analysis", "suggestion"]

    def __str__(self) -> str:
        return f"Corte #{self.id} – {self.suggestion.title or self.suggestion.start_tc}"
