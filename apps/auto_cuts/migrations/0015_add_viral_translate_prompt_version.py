# Generated manually - Viral Translate EN to PT

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auto_cuts', '0014_autocutanalysis_target_brand'),
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
                    ('viral_translate', 'Viral Translate - EN to PT'),
                ],
                default='viral',
                help_text='Modelo de prompt e idioma: PT, EN ou EN→PT com legendas',
                max_length=24,
            ),
        ),
    ]
