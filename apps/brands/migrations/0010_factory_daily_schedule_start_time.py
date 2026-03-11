from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0009_brandyoutubecredential"),
    ]

    operations = [
        migrations.AddField(
            model_name="factory",
            name="daily_schedule_start_time",
            field=models.TimeField(
                blank=True,
                default=None,
                help_text="Horário local (timezone da factory) para início do agendamento diário. Se já passou e o dia ainda não foi agendado, o sistema tenta a cada 30 min.",
                null=True,
            ),
        ),
    ]
