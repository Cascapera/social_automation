# Migração: cut (FK) -> cuts (M2M through JobCut)

import django.db.models.deletion
from django.db import migrations, models


def migrate_cut_to_jobcut(apps, schema_editor):
    """Cria JobCut para cada Job existente a partir do cut antigo."""
    Job = apps.get_model("jobs", "Job")
    JobCut = apps.get_model("jobs", "JobCut")
    for job in Job.objects.all():
        if job.cut_id:
            JobCut.objects.create(job=job, cut_id=job.cut_id, order=0)


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0002_add_transition_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobCut",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveSmallIntegerField(default=0, help_text="Ordem na sequência (0, 1, 2...)")),
                ("cut", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="job_cuts", to="cuts.cut")),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="job_cuts", to="jobs.job")),
            ],
            options={
                "ordering": ["order"],
                "verbose_name": "Corte do job",
                "verbose_name_plural": "Cortes do job",
            },
        ),
        migrations.RunPython(migrate_cut_to_jobcut, migrations.RunPython.noop),
        migrations.RemoveField(model_name="job", name="cut"),
        migrations.AddField(
            model_name="job",
            name="cuts",
            field=models.ManyToManyField(
                blank=True,
                help_text="Cortes na ordem: intro → corte1 → corte2 → ... → outro",
                related_name="jobs",
                through="jobs.JobCut",
                to="cuts.cut",
            ),
        ),
        migrations.AddConstraint(
            model_name="jobcut",
            constraint=models.UniqueConstraint(fields=("job", "order"), name="jobs_jobcut_unique_job_order"),
        ),
    ]
