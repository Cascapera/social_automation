# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0029_autocutanalysis_ready_cuts_titles_language"),
        ("brands", "0030_brandasset_overlay_long_choice"),
    ]

    operations = [
        migrations.AddField(
            model_name="autocutanalysis",
            name="long_overlay_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Sobrepõe imagem ou MP4 na lateral direita dos cortes longos (16:9), alinhado ao topo e à direita.",
            ),
        ),
        migrations.AddField(
            model_name="autocutanalysis",
            name="long_overlay_asset",
            field=models.ForeignKey(
                blank=True,
                help_text="Asset OVERLAY_LONG da brand; usado só se long_overlay_enabled.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="brands.brandasset",
            ),
        ),
    ]
