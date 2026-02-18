# Remove UNIQUE (job_id, order) - ordem = ordem de adição (id)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0003_multiple_cuts"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="jobcut",
            name="jobs_jobcut_unique_job_order",
        ),
    ]
