# Factory OAuth para busca e regra de idade mínima do vídeo

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0016_search_channel_and_auto_fetch"),
    ]

    operations = [
        migrations.AddField(
            model_name="factory",
            name="auto_fetch_min_video_age_hours",
            field=models.PositiveSmallIntegerField(
                default=24,
                help_text="Mínimo de horas desde a publicação do vídeo original para criar job. Política de direitos: 24h.",
            ),
        ),
        migrations.CreateModel(
            name="FactoryYouTubeCheckCredential",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel_id", models.CharField(blank=True, default="", max_length=64)),
                ("account_name", models.CharField(blank=True, default="", max_length=120)),
                ("access_token", models.TextField(blank=True, default="")),
                ("refresh_token", models.TextField(blank=True, default="")),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "factory",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="youtube_check_credential",
                        to="brands.factory",
                    ),
                ),
            ],
            options={
                "verbose_name": "Credencial YouTube Check da Factory",
                "verbose_name_plural": "Credenciais YouTube Check da Factory",
            },
        ),
    ]
