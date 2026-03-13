# Canais de busca e auto-fetch de vídeos

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0015_remove_brand_interval_and_max_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="factory",
            name="auto_fetch_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Quando ativo, busca automaticamente vídeos nos canais de busca quando o banco está abaixo dos mínimos.",
            ),
        ),
        migrations.AddField(
            model_name="factory",
            name="auto_fetch_min_per_brand",
            field=models.PositiveSmallIntegerField(
                default=3,
                help_text="Mínimo de vídeos AVAILABLE por brand. Ao atingir, busca em fontes que direcionam para essa brand.",
            ),
        ),
        migrations.AddField(
            model_name="factory",
            name="auto_fetch_min_total",
            field=models.PositiveSmallIntegerField(
                default=10,
                help_text="Mínimo total de vídeos AVAILABLE na factory. Ao atingir, busca em qualquer fonte.",
            ),
        ),
        migrations.AddField(
            model_name="factory",
            name="auto_fetch_max_total",
            field=models.PositiveSmallIntegerField(
                default=100,
                help_text="Máximo de vídeos no banco. Evita loop infinito quando alguma brand tem estoque baixo.",
            ),
        ),
        migrations.CreateModel(
            name="SearchChannel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "youtube_channel_url",
                    models.CharField(
                        help_text="URL do canal (ex: youtube.com/@flowpodcast ou youtube.com/channel/UC...)",
                        max_length=500,
                    ),
                ),
                (
                    "youtube_channel_id",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="ID do canal no YouTube (preenchido ao validar/salvar).",
                        max_length=64,
                    ),
                ),
                (
                    "channel_title",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Nome do canal (preenchido ao buscar).",
                        max_length=200,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Se inativo, não busca vídeos neste canal.",
                    ),
                ),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "factory",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search_channels",
                        to="brands.factory",
                    ),
                ),
                (
                    "target_brand",
                    models.ForeignKey(
                        blank=True,
                        help_text="Se preenchido, vídeos vão para este canal. Se vazio, usa theme_category da IA (todos).",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="search_channels_targeted",
                        to="brands.brand",
                    ),
                ),
            ],
            options={
                "verbose_name": "Canal de busca",
                "verbose_name_plural": "Canais de busca",
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="ProcessedChannelVideo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("youtube_video_id", models.CharField(max_length=32)),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "factory",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="processed_channel_videos",
                        to="brands.factory",
                    ),
                ),
                (
                    "search_channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="processed_videos",
                        to="brands.searchchannel",
                    ),
                ),
            ],
            options={
                "verbose_name": "Vídeo processado",
                "verbose_name_plural": "Vídeos processados",
                "unique_together": {("search_channel", "youtube_video_id")},
            },
        ),
    ]
