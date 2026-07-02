from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "jobs"

    def ready(self) -> None:
        # Connect the post_delete handlers that clean up storage objects.
        from . import signals  # noqa: F401
