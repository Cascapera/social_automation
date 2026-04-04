# Generated manually for ready cuts batch

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0027_add_viral_long_prompt_versions"),
    ]

    operations = [
        migrations.AddField(
            model_name="autocutanalysis",
            name="ready_cuts_create_long_video",
            field=models.BooleanField(
                default=False,
                help_text="Montar um vídeo longo horizontal juntando os arquivos (fade entre clipes).",
            ),
        ),
        migrations.AddField(
            model_name="autocutanalysis",
            name="ready_cuts_long_fade_duration",
            field=models.FloatField(
                default=0.5,
                help_text="Duração do fade (s) entre clipes no vídeo longo.",
            ),
        ),
        migrations.AddField(
            model_name="autocutanalysis",
            name="ready_cuts_transcribe",
            field=models.BooleanField(
                default=True,
                help_text="Cortes prontos em lote: transcrever e gerar legendas (Whisper). Se falso, títulos vêm só do nome do job.",
            ),
        ),
        migrations.CreateModel(
            name="AutoCutReadyChunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order_index", models.PositiveSmallIntegerField(default=0)),
                ("file", models.FileField(max_length=255, upload_to="auto_cuts/ready_chunks/")),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                ("transcript", models.TextField(blank=True, default="")),
                (
                    "transcript_segments",
                    models.JSONField(
                        blank=True,
                        help_text="Segmentos Whisper [{start, end, text}] relativos a este arquivo",
                        null=True,
                    ),
                ),
                (
                    "analysis",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ready_chunks",
                        to="auto_cuts.autocutanalysis",
                    ),
                ),
            ],
            options={
                "ordering": ["analysis_id", "order_index", "id"],
            },
        ),
    ]
