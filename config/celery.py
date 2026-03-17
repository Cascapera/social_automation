import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "social_automation.settings")

app = Celery("social_automation")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "check-scheduled-posts": {
        "task": "apps.social.tasks.check_scheduled_posts_task",
        "schedule": 60.0,  # a cada 60 segundos
    },
    "reconcile-youtube-schedules": {
        "task": "apps.social.tasks.reconcile_youtube_schedules_task",
        "schedule": 300.0,  # a cada 5 min (economiza cota: videos.list = 1 unidade por post)
    },
    # Cron fixo às 19h. Se servidor cair ou der erro, use o botão "Agendamento Imediato".
    "generate-daily-factory-schedules": {
        "task": "apps.social.tasks.generate_daily_factory_schedules_task",
        "schedule": crontab(hour=19, minute=0),
    },
    # Busca automática de vídeos nos canais de busca (quando auto_fetch_enabled).
    "check-and-fetch-new-videos": {
        "task": "apps.jobs.tasks_auto_fetch.check_and_fetch_new_videos_task",
        "schedule": 900.0,  # a cada 15 min
    },
    # Limpeza de mídias de vídeos já postados (cortes, job output, análise).
    "cleanup-posted-media": {
        "task": "apps.social.tasks.cleanup_posted_media_task",
        "schedule": crontab(minute=0, hour="*/4"),  # a cada 4 horas
    },
}