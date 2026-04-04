from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0009_autocutanalysis_targets"),
    ]

    operations = [
        migrations.AddField(
            model_name="autocutanalysis",
            name="thumbnail_font",
            field=models.CharField(
                choices=[
                    ("anton", "Anton"),
                    ("bebas", "Bebas Neue"),
                    ("montserrat", "Montserrat ExtraBold"),
                    ("impact", "Impact"),
                ],
                default="impact",
                help_text="Fonte usada na thumbnail automática.",
                max_length=20,
            ),
        ),
    ]
