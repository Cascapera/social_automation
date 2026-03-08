from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0006_factory_scheduling_paused"),
    ]

    operations = [
        migrations.AddField(
            model_name="factory",
            name="processing_paused",
            field=models.BooleanField(
                default=False,
                help_text="Quando ativo, impede início de novos jobs de cortes automáticos na factory.",
            ),
        ),
    ]
