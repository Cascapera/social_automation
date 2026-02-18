# Generated manually for transition fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='transition',
            field=models.CharField(
                choices=[
                    ('none', 'Nenhuma'),
                    ('fade', 'Fade (crossfade)'),
                    ('fadeblack', 'Fade por preto'),
                    ('wipeleft', 'Wipe esquerda'),
                    ('wiperight', 'Wipe direita'),
                    ('dissolve', 'Dissolve'),
                ],
                default='none',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='transition_duration',
            field=models.DecimalField(
                decimal_places=1,
                default=0.5,
                help_text='Duração em segundos (ex: 0.5, 1.0, 0.2)',
                max_digits=3,
            ),
        ),
    ]
