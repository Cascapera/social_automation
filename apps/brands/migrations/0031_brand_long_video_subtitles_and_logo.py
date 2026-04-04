from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0030_brandasset_overlay_long_choice"),
    ]

    operations = [
        migrations.AddField(
            model_name="brand",
            name="long_video_subtitles_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Se ativo, queima legendas nos cortes longos horizontais (16:9) na finalização.",
            ),
        ),
        migrations.AddField(
            model_name="brand",
            name="long_video_logo_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Se ativo, insere o logo da marca nos cortes longos horizontais na finalização.",
            ),
        ),
    ]
