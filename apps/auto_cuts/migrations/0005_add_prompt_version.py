# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auto_cuts', '0004_add_autocutcorte'),
    ]

    operations = [
        migrations.AddField(
            model_name='autocutanalysis',
            name='prompt_version',
            field=models.CharField(
                choices=[('viral', 'Viral/Engajamento'), ('educational', 'Educacional/Financeiro')],
                default='viral',
                help_text='Modelo de prompt para análise: viral (polêmico) ou educational (didático)',
                max_length=20,
            ),
        ),
    ]
