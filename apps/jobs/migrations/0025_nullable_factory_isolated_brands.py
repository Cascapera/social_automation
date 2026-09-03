import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('brands', '0001_initial'),
        ('jobs', '0024_pipelineexecution_attempt_number'),
    ]

    operations = [
        migrations.AlterField(
            model_name='videoinventoryitem',
            name='factory',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='video_inventory_items',
                to='brands.factory',
            ),
        ),
        migrations.AlterField(
            model_name='factorypostingschedule',
            name='factory',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='posting_schedules',
                to='brands.factory',
            ),
        ),
        migrations.AlterField(
            model_name='postedvideolog',
            name='factory',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='posted_video_logs',
                to='brands.factory',
            ),
        ),
    ]
