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
        "schedule": 60.0,  # every 60 seconds
    },
    "reconcile-youtube-schedules": {
        "task": "apps.social.tasks.reconcile_youtube_schedules_task",
        "schedule": 300.0,  # every 5 min (saves quota: videos.list = 1 unit per post)
    },
    # Checks every 5 min, but only schedules during the local 09h / 11h / 13h windows.
    "generate-daily-factory-schedules": {
        "task": "apps.social.tasks.generate_daily_factory_schedules_task",
        "schedule": 300.0,
    },
    # Automatic fetch of videos from search channels (when auto_fetch_enabled).
    "check-and-fetch-new-videos": {
        "task": "apps.jobs.tasks_auto_fetch.check_and_fetch_new_videos_task",
        "schedule": 900.0,  # every 15 min
    },
    # Cleanup of media for already-posted videos (cuts, job output, analysis).
    # Re-enabled after hardening (Passos A/B/C): gates em DONE posts, AutoCutReadyChunk
    # em refs, mtime guard 24h e canary tests.
    "cleanup-posted-media": {
        "task": "apps.social.tasks.cleanup_posted_media_task",
        "schedule": crontab(minute=0, hour="*/4"),  # every 4 hours
    },
}
