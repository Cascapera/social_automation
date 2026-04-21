from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0023_deadletterjob'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='pipelineexecution',
            name='jobs_pipe_exec_scope_uniq',
        ),
        migrations.AddField(
            model_name='pipelineexecution',
            name='attempt_number',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddConstraint(
            model_name='pipelineexecution',
            constraint=models.UniqueConstraint(
                fields=('pipeline_type', 'aggregate_type', 'aggregate_id', 'attempt_number'),
                name='jobs_pipe_exec_scope_uniq',
            ),
        ),
    ]
