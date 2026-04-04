from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0008_autocutcorte_thumbnail"),
    ]

    operations = [
        migrations.AddField(
            model_name="autocutanalysis",
            name="longs_target",
            field=models.PositiveSmallIntegerField(
                default=3,
                help_text="Quantidade de cortes longos para manter (1-10).",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(10),
                ],
            ),
        ),
        migrations.AddField(
            model_name="autocutanalysis",
            name="shorts_target",
            field=models.PositiveSmallIntegerField(
                default=12,
                help_text="Quantidade de cortes curtos para manter (1-30).",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(30),
                ],
            ),
        ),
    ]
