from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0007_factory_processing_paused"),
    ]

    operations = [
        migrations.AddField(
            model_name="brand",
            name="youtube_client_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Client ID OAuth do Google para esta brand/canal (opcional; fallback no .env).",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="brand",
            name="youtube_client_secret",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Client Secret OAuth do Google para esta brand/canal (opcional; fallback no .env).",
            ),
        ),
        migrations.AddField(
            model_name="brand",
            name="youtube_redirect_uri",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Redirect URI OAuth para esta brand/canal (opcional; fallback no .env).",
                max_length=500,
            ),
        ),
    ]
