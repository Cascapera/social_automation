# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0028_autocutreadychunk_and_ready_cuts_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="autocutanalysis",
            name="ready_cuts_titles_language",
            field=models.CharField(
                choices=[("pt", "Português"), ("en", "English")],
                default="pt",
                help_text="Idioma dos títulos gerados pela LLM no job de cortes prontos (lote).",
                max_length=2,
            ),
        ),
    ]
