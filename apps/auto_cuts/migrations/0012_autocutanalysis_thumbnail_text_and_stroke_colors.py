from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0011_autocutanalysis_thumbnail_band_color"),
    ]

    operations = [
        migrations.AddField(
            model_name="autocutanalysis",
            name="thumbnail_stroke_color",
            field=models.CharField(
                default="#FFEBDC",
                help_text="Cor do contorno/efeito do texto da thumbnail em HEX (#RRGGBB).",
                max_length=7,
            ),
        ),
        migrations.AddField(
            model_name="autocutanalysis",
            name="thumbnail_text_color",
            field=models.CharField(
                default="#0A0A0A",
                help_text="Cor principal do texto da thumbnail em HEX (#RRGGBB).",
                max_length=7,
            ),
        ),
    ]
