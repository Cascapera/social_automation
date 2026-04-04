from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0010_autocutanalysis_thumbnail_font"),
    ]

    operations = [
        migrations.AddField(
            model_name="autocutanalysis",
            name="thumbnail_band_color",
            field=models.CharField(
                default="#E12E20",
                help_text="Cor da faixa inferior da thumbnail em HEX (#RRGGBB).",
                max_length=7,
            ),
        ),
    ]
