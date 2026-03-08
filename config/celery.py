import os
from celery import Celery
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "social_automation.settings")

app = Celery("social_automation")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "check-scheduled-posts": {
        "task": "apps.social.tasks.check_scheduled_posts_task",
        "schedule": 60.0,  # a cada 60 segundos
    },
    "generate-daily-factory-schedules": {
        "task": "apps.social.tasks.generate_daily_factory_schedules_task",
        "schedule": 300.0,  # varre factories a cada 5 minutos
    },
}