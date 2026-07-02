"""Project package.

Importing the Celery app here ensures the shared task queue is configured as
soon as Django starts, so ``@shared_task`` decorators bind to the right app.
"""
from .celery import app as celery_app

__all__ = ("celery_app",)
