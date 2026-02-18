# Migração: platform (único) -> target_platforms (múltiplas redes)

from django.db import migrations, models


def migrate_platform_to_target_platforms(apps, schema_editor):
    """Copia o valor de platform para target_platforms como lista única."""
    Job = apps.get_model("jobs", "Job")
    for job in Job.objects.all():
        if hasattr(job, "platform") and job.platform:
            job.target_platforms = [job.platform]
        else:
            job.target_platforms = ["YT"]
        job.save()


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0004_remove_jobcut_unique_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="target_platforms",
            field=models.JSONField(
                default=list,
                help_text="Redes onde o vídeo será postado automaticamente. Marque as que deseja usar.",
            ),
        ),
        migrations.RunPython(migrate_platform_to_target_platforms, migrations.RunPython.noop),
        migrations.RemoveField(model_name="job", name="platform"),
    ]
