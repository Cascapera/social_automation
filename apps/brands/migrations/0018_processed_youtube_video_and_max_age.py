# ProcessedYoutubeVideo global e max idade do vídeo

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0017_factory_youtube_check_and_min_age"),
    ]

    operations = [
        migrations.AddField(
            model_name="factory",
            name="auto_fetch_max_video_age_hours",
            field=models.PositiveSmallIntegerField(
                default=168,
                help_text="Máximo de horas desde a publicação. Vídeos mais antigos não são processados (tema esfriou). 168h = 7 dias.",
            ),
        ),
        migrations.CreateModel(
            name="ProcessedYoutubeVideo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("youtube_video_id", models.CharField(max_length=32)),
                (
                    "source",
                    models.CharField(
                        choices=[("manual", "Manual"), ("auto", "Automático")],
                        default="manual",
                        max_length=20,
                    ),
                ),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "factory",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="processed_youtube_videos",
                        to="brands.factory",
                    ),
                ),
            ],
            options={
                "verbose_name": "Vídeo YouTube processado",
                "verbose_name_plural": "Vídeos YouTube processados",
                "unique_together": {("factory", "youtube_video_id")},
            },
        ),
    ]
