# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0028_brand_upload_post_youtube_enabled"),
    ]

    operations = [
        migrations.AlterField(
            model_name="factory",
            name="auto_fetch_prompt_version",
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
                help_text="Modo de análise usado nos jobs criados pela busca automática (válido para todas as brands da factory).",
                max_length=24,
            ),
        ),
    ]
