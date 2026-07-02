"""Django settings for the NL-to-Regex data-processing platform.

Split into focused submodules (this package composes them via star-imports so
``DJANGO_SETTINGS_MODULE=config.settings`` keeps resolving exactly as it did
when this was a single file):

    env.py       -- BASE_DIR, _env_bool (shared helpers; imported directly by
                    the other submodules, not re-exported here)
    base.py      -- core Django config: security, apps, middleware, templates,
                    i18n/timezone, static files, logging, DRF, CORS, ingest limits
    storage.py   -- STORAGE_BACKEND, DATA_DIR/UPLOAD_DIR/RESULTS_DIR, S3 config
    database.py  -- DATABASES (Neon / sqlite-for-tests)
    redis.py     -- REDIS_URL, CELERY_*, CACHES
    spark.py     -- SPARK_*
    llm.py       -- ANTHROPIC_API_KEY, LLM_*, REGEX_CACHE_TTL

``base`` is loaded first since it defines BASE_DIR-derived settings like
STATIC_ROOT; the remaining submodules are independent of each other and of
``base``, each computing what it needs (e.g. BASE_DIR) from ``env`` directly.
See ``.env.example`` for the full list of knobs.
"""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .storage import *  # noqa: F401,F403
from .database import *  # noqa: F401,F403
from .redis import *  # noqa: F401,F403
from .spark import *  # noqa: F401,F403
from .llm import *  # noqa: F401,F403
