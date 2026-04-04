# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0026_autocutanalysis_vertical_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="autocutanalysis",
            name="prompt_version",
            field=models.CharField(
                choices=[
                    ("viral", "Viral (PT)"),
                    ("viral_long", "Viral longo (PT, shorts 90–160s)"),
                    ("educational", "Educacional (PT)"),
                    ("viral_en", "Viral (EN)"),
                    ("viral_long_en", "Viral longo (EN, shorts 90–160s)"),
                    ("educational_en", "Educacional (EN)"),
                    ("viral_translate", "Viral Translate - EN to PT"),
                ],
                default="viral",
                help_text="Modelo de prompt e idioma: PT, EN ou EN→PT com legendas; viral longo = shorts 90–160s",
                max_length=24,
            ),
        ),
    ]
