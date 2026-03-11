# Generated manually - slots fixos definem limite; intervalo usa default interno

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0014_brand_long_slot_times_remove_windows"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="brand",
            name="min_short_interval_minutes",
        ),
        migrations.RemoveField(
            model_name="brand",
            name="min_long_interval_minutes",
        ),
        migrations.RemoveField(
            model_name="brand",
            name="max_shorts_per_day",
        ),
        migrations.RemoveField(
            model_name="brand",
            name="max_longs_per_day",
        ),
    ]
