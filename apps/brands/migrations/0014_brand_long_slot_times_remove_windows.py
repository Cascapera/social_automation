# Generated manually - slots fixos obrigatórios, remove janelas dinâmicas

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0013_brand_short_slot_times"),
    ]

    operations = [
        migrations.AddField(
            model_name="brand",
            name="long_slot_times",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Horários fixos de vídeos longos por dia (ex: ['20:00']). Obrigatório para agendar longos.",
            ),
        ),
        migrations.RemoveField(
            model_name="brand",
            name="short_window_start",
        ),
        migrations.RemoveField(
            model_name="brand",
            name="short_window_end",
        ),
        migrations.RemoveField(
            model_name="brand",
            name="long_window_start",
        ),
        migrations.RemoveField(
            model_name="brand",
            name="long_window_end",
        ),
    ]
