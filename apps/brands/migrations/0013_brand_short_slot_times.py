# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0012_alter_factory_daily_schedule_start_time"),
    ]

    operations = [
        migrations.AddField(
            model_name="brand",
            name="short_slot_times",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Horários fixos de shorts por dia (ex: ['10:00', '14:00', '18:00']). Prioridade sobre janela.",
            ),
        ),
    ]
