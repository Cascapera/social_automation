# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0029_add_viral_long_auto_fetch_prompt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="brandasset",
            name="asset_type",
            field=models.CharField(
                choices=[
                    ("LOGO", "Logo"),
                    ("FRAME", "Frame/Moldura"),
                    ("INTRO", "Intro vídeo"),
                    ("OUTRO", "Outro vídeo"),
                    ("CTA", "CTA vídeo/imagem"),
                    ("ANIMATION", "Animação overlay (PNG/GIF com transparência)"),
                    ("OVERLAY_LONG", "Overlay vídeo longo (direita: PNG/JPG/MP4)"),
                    ("THUMB_SHORT", "Thumb Shorts"),
                    ("THUMB_LONG", "Thumb Longs"),
                ],
                max_length=16,
            ),
        ),
    ]
