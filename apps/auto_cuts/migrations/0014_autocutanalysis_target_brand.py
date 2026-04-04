# Generated manually

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('brands', '0001_initial'),
        ('auto_cuts', '0013_autocutsuggestion_source_asset_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='autocutanalysis',
            name='target_brand',
            field=models.ForeignKey(
                blank=True,
                help_text='Quando definido, todos os cortes deste vídeo vão para este canal (ignora theme_category da IA).',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='auto_cut_analyses_targeted',
                to='brands.brand',
            ),
        ),
    ]
