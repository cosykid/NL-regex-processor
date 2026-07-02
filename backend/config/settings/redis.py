"""Redis — broker, result backend, and cache all live here on separate DBs."""
from __future__ import annotations

import os

from .env import _env_bool

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379").rstrip("/")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", f"{REDIS_URL}/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", f"{REDIS_URL}/1")
REDIS_CACHE_URL = os.environ.get("REDIS_CACHE_URL", f"{REDIS_URL}/2")

CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_RESULT_EXTENDED = True
# A regex+Spark job can run for a while; give it room before the broker
# considers it lost and re-delivers it.
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ALWAYS_EAGER = _env_bool("CELERY_TASK_ALWAYS_EAGER", False)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
    }
}
