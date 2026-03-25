# Generated manually: limpa FK órfão após exclusão de BrandAsset fora do fluxo esperado

from django.db import migrations
from django.db.models import Exists, OuterRef


def _clear_orphan_long_overlay(apps, schema_editor):
    AutoCutAnalysis = apps.get_model("auto_cuts", "AutoCutAnalysis")
    BrandAsset = apps.get_model("brands", "BrandAsset")
    bad = (
        AutoCutAnalysis.objects.filter(long_overlay_asset_id__isnull=False)
        .annotate(_ok=Exists(BrandAsset.objects.filter(pk=OuterRef("long_overlay_asset_id"))))
        .filter(_ok=False)
    )
    bad.update(long_overlay_asset_id=None, long_overlay_enabled=False)


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("auto_cuts", "0030_autocutanalysis_long_overlay"),
        ("brands", "0030_brandasset_overlay_long_choice"),
    ]

    operations = [
        migrations.RunPython(_clear_orphan_long_overlay, _noop),
    ]
