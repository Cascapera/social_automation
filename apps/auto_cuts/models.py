from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.brands.models import Brand, BrandAsset
from apps.mediahub.models import SourceVideo


class AutoCutAnalysis(models.Model):
    """Análise de vídeo para sugestão automática de cortes virais."""

    STATUS = [
        ("pending", "Pendente"),
        ("transcribing", "Transcrevendo"),
        ("analyzing", "Analisando"),
        ("finalizing", "Finalizando"),
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
    distribution_mode = models.CharField(
        max_length=20,
        choices=[
            ("theme", "Por tema (IA)"),
            ("distribute", "Distribuir pelas Brands"),
        ],
        default="theme",
        help_text="Quando target_brand vazio: 'theme' usa categoria da IA; 'distribute' envia para a brand com menos vídeos no banco.",
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
            ("viral_long", "Viral longo (PT, shorts 90–160s)"),
            ("educational", "Educacional (PT)"),
            ("viral_en", "Viral (EN)"),
            ("viral_long_en", "Viral longo (EN, shorts 90–160s)"),
            ("educational_en", "Educacional (EN)"),
            ("viral_translate", "Viral Translate - EN to PT"),
        ],
        default="viral",
        help_text="Modelo de prompt e idioma: PT, EN ou EN→PT com legendas; viral longo = shorts 90–160s",
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
    is_ready_cuts = models.BooleanField(
        default=False,
        help_text="Vídeo(s) já editados: transcreve, LLM retorna só metadata (title, virality, thumbnail), finaliza. Sem extração de segmentos.",
    )
    ready_cuts_transcribe = models.BooleanField(
        default=True,
        help_text="Cortes prontos em lote: transcrever e gerar legendas (Whisper). Se falso, títulos vêm só do nome do job.",
    )
    ready_cuts_create_long_video = models.BooleanField(
        default=False,
        help_text="Montar um vídeo longo horizontal juntando os arquivos (fade entre clipes).",
    )
    ready_cuts_long_fade_duration = models.FloatField(
        default=0.5,
        help_text="Duração do fade (s) entre clipes no vídeo longo.",
    )
    ready_cuts_titles_language = models.CharField(
        max_length=2,
        choices=[("pt", "Português"), ("en", "English")],
        default="pt",
        help_text="Idioma dos títulos gerados pela LLM no job de cortes prontos (lote).",
    )
    vertical_mode = models.CharField(
        max_length=20,
        choices=[("zoom_crop", "Zoom e corte"), ("frame_center", "Enquadrar e centralizar")],
        default="zoom_crop",
        blank=True,
        help_text="Modo de reenquadramento vertical para shorts (16:9 → 9:16). Zoom preenche a tela; Enquadrar adiciona bordas e logo.",
    )
    long_overlay_enabled = models.BooleanField(
        default=False,
        help_text="Sobrepõe imagem ou MP4 na lateral direita dos cortes longos (16:9), alinhado ao topo e à direita.",
    )
    long_overlay_asset = models.ForeignKey(
        BrandAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Asset OVERLAY_LONG da brand; usado só se long_overlay_enabled.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "updated_at" not in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"updated_at"}
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"AutoCut #{self.id} – {self.name or 'Sem nome'} ({self.status})"

    @property
    def video_file(self):
        """Retorna o arquivo de vídeo (source ou upload direto)."""
        if self.source and self.source.file:
            return self.source.file
        return self.file


class AutoCutReadyChunk(models.Model):
    """Arquivo de vídeo no job de cortes prontos (lote); ordem define a edição do vídeo longo."""

    analysis = models.ForeignKey(
        AutoCutAnalysis,
        on_delete=models.CASCADE,
        related_name="ready_chunks",
    )
    order_index = models.PositiveSmallIntegerField(default=0)
    file = models.FileField(upload_to="auto_cuts/ready_chunks/", max_length=255)
    duration_seconds = models.FloatField(null=True, blank=True)
    transcript = models.TextField(blank=True, default="")
    transcript_segments = models.JSONField(
        null=True,
        blank=True,
        help_text="Segmentos Whisper [{start, end, text}] relativos a este arquivo",
    )

    class Meta:
        ordering = ["analysis_id", "order_index", "id"]

    def __str__(self) -> str:
        return f"ReadyChunk #{self.id} job={self.analysis_id} ord={self.order_index}"


class AutoCutSuggestion(models.Model):
    """Sugestão de corte viral gerada pela análise."""

    CUT_TYPE = [
        ("short", "Curto (Reels/TikTok/Shorts)"),
        ("long", "Longo (YouTube)"),
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
        blank=True,
        default="",
        help_text="Code da BrandCategory (por factory) retornado pela LLM.",
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
