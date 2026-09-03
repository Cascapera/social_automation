from django.apps import AppConfig


class MultipleCreatorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.multiple_creator"
    verbose_name = "Multiple-Creator"

    def ready(self):
        # Registra o signal que fecha BrandExecution quando AutoCutAnalysis finaliza.
        from . import signals  # noqa: F401
