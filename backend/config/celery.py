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
from celery.signals import worker_process_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("nl_regex_processor")

# All Celery settings live in Django settings under the CELERY_ namespace.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover tasks.py in every installed app.
app.autodiscover_tasks()


@worker_process_init.connect
def _warm_spark(**_kwargs) -> None:
    """Boot the JVM/SparkSession in each prefork child at startup.

    Fires once per worker process (the process that actually runs tasks), so
    the first job finds a hot Spark session instead of blocking on the cold
    JVM start. Skipped in eager mode (tests run inline, no worker) and when
    ``SPARK_WARMUP`` is disabled.
    """
    from django.conf import settings

    if settings.CELERY_TASK_ALWAYS_EAGER or not settings.SPARK_WARMUP:
        return
    from processing import spark_engine

    spark_engine.warm_spark()


@app.task(bind=True)
def debug_task(self) -> str:  # pragma: no cover - operational helper
    return f"request: {self.request!r}"
