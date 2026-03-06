# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auto_cuts', '0006_add_youtube_url'),
    ]

    operations = [
        migrations.AlterField(
            model_name='autocutanalysis',
            name='prompt_version',
            field=models.CharField(
                choices=[
                    ('viral', 'Viral (PT)'),
                    ('educational', 'Educacional (PT)'),
                    ('viral_en', 'Viral (EN)'),
                    ('educational_en', 'Educacional (EN)'),
                ],
                default='viral',
                help_text='Modelo de prompt e idioma: PT ou EN',
                max_length=20,
            ),
        ),
    ]
