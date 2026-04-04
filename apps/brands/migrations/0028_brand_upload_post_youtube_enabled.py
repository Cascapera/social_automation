# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0027_factory_auto_fetch_last_empty_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="brand",
            name="upload_post_youtube_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Se ativo, envia vídeos para YouTube via Upload-Post (preferência). Se inativo, usa YouTube API/OAuth direto.",
            ),
        ),
    ]
