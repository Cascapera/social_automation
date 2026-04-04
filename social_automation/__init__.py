from config.celery import app as celery_app


def _register_celery_observability() -> None:
    # Keep Celery app initialization first, then hook lightweight signals.
    from apps.common.task_observability import register_celery_observability_signal_handlers

    register_celery_observability_signal_handlers()


_register_celery_observability()

__all__ = ("celery_app",)
