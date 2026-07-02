"""Celery application bootstrap.

Broker / result-backend / cache all live in Redis (see ``config.settings``):

    redis://<host>:6379/0   broker        (task queue)
    redis://<host>:6379/1   result backend (task state + return values)
    redis://<host>:6379/2   cache          (LLM regex cache, cancel flags)

The web process only ever *enqueues* tasks; every heavy operation (file
parsing, LLM regex generation, Spark replacement) runs in the worker.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("nl_regex_processor")

# All Celery settings live in Django settings under the CELERY_ namespace.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover tasks.py in every installed app.
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self) -> str:  # pragma: no cover - operational helper
    return f"request: {self.request!r}"
