# Preserva registros de agendamento ao deletar job ou vídeo (apenas mídia é removida)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0016_factorypostingschedule_factorypostingattemptlog_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='scheduledpost',
            name='job',
            field=models.ForeignKey(
                blank=True,
                help_text='Job de origem. SET_NULL ao deletar job para preservar histórico de agendamento.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='scheduled_posts',
                to='jobs.job',
            ),
        ),
        migrations.AlterField(
            model_name='scheduledpost',
            name='auto_cut_corte',
            field=models.ForeignKey(
                blank=True,
                help_text='Corte finalizado do Auto Cuts (opcional). SET_NULL ao deletar para preservar histórico.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='scheduled_posts',
                to='auto_cuts.autocutcorte',
            ),
        ),
        migrations.AlterField(
            model_name='videoinventoryitem',
            name='auto_cut_corte',
            field=models.OneToOneField(
                blank=True,
                help_text='Corte de origem. SET_NULL ao deletar para preservar histórico de agendamento.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='inventory_item',
                to='auto_cuts.autocutcorte',
            ),
        ),
    ]
