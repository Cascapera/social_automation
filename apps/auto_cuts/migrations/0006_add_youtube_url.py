# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auto_cuts', '0005_add_prompt_version'),
    ]

    operations = [
        migrations.AddField(
            model_name='autocutanalysis',
            name='youtube_url',
            field=models.URLField(
                blank=True,
                default="",
                help_text="URL do YouTube (alternativa ao upload/source)",
                max_length=500,
            ),
        ),
    ]
