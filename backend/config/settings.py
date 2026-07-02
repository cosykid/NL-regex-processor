"""Django settings for the NL-to-Regex data-processing platform.

Configuration is environment-driven so the same image runs locally, in
docker-compose, and in a deployed environment. See ``.env.example`` for the
full list of knobs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    # This is a token-less JSON API with no admin site, login, or sessions, so
    # Django's admin / auth / contenttypes / sessions / messages apps are
    # intentionally omitted — their (empty) tables are not created.
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "corsheaders",
    # local
    "jobs",
    "processing",
    "api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

# --------------------------------------------------------------------------- #
# Object storage backend for uploads + Spark results.
#
#   local (default) -> files under DATA_DIR (below). No AWS needed; used by tests.
#   s3              -> an S3 bucket (provisioned by infra/terraform). Auth uses
#                      the default AWS credential chain: static keys locally,
#                      an IAM role when deployed — no code change between them.
# --------------------------------------------------------------------------- #
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").strip().lower()

# Local-backend storage roots (uploads + Spark results). Always defined — the
# local backend and the test suite reference these paths — but only materialized
# on disk in local mode; in s3 mode nothing writes here, so we don't create it.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR.parent / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
RESULTS_DIR = DATA_DIR / "results"
if STORAGE_BACKEND == "local":
    for _d in (DATA_DIR, UPLOAD_DIR, RESULTS_DIR):
        _d.mkdir(parents=True, exist_ok=True)
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_REGION = (
    os.environ.get("S3_REGION")
    or os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "ap-southeast-2"
)
# Optional S3-compatible endpoint (MinIO / LocalStack). Empty = real AWS S3.
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")

if STORAGE_BACKEND not in {"local", "s3"}:
    raise ImproperlyConfigured(
        f"STORAGE_BACKEND must be 'local' or 's3', got '{STORAGE_BACKEND}'."
    )
if STORAGE_BACKEND == "s3" and not S3_BUCKET:
    raise ImproperlyConfigured(
        "STORAGE_BACKEND=s3 requires S3_BUCKET. Provision it with "
        "infra/terraform and copy the `dotenv_snippet` output into your .env."
    )

# --------------------------------------------------------------------------- #
# Database — Neon is the single source of truth.
#
# Jobs (status, progress, resolved regex, result metadata) are persisted to a
# managed Postgres / Neon instance resolved from NEON_DATABASE_URL / DATABASE_URL.
# There is intentionally **no** SQLite or local-Postgres fallback for the app:
# DATABASE_URL is required and the app refuses to start without it. The one
# exception is the test suite — when running under pytest with no DATABASE_URL,
# an in-memory SQLite is used so the hermetic suite needs no external database.
# --------------------------------------------------------------------------- #
def _database_from_url(url: str) -> dict:
    """Parse a Postgres connection URL (e.g. a Neon ``DATABASE_URL``)."""
    from urllib.parse import parse_qs, unquote, urlparse

    parts = urlparse(url)
    query = parse_qs(parts.query)
    # Neon (like any managed Postgres) requires TLS; default to it unless the
    # URL explicitly says otherwise. The other libpq params that Neon emits in
    # its copy-paste connection string are passed straight through so the URL is
    # honoured exactly as given (e.g. `channel_binding=require`).
    options = {"sslmode": query.get("sslmode", ["require"])[0]}
    for _passthrough in ("channel_binding", "options", "connect_timeout"):
        if _passthrough in query:  # e.g. Neon's `?options=endpoint%3Dep-...`
            options[_passthrough] = query[_passthrough][0]
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parts.path.lstrip("/")) or "nlregex",
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or 5432),
        "OPTIONS": options,
        "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "600")),
    }


_NEON_DATABASE_URL = os.environ.get("NEON_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", ""
)
_RUNNING_TESTS = "pytest" in sys.modules or "test" in sys.argv

if _NEON_DATABASE_URL:
    DATABASES = {"default": _database_from_url(_NEON_DATABASE_URL)}
    if _RUNNING_TESTS:
        # Force short-lived connections so a lingering persistent connection
        # can't block the DROP of the isolated `test_<name>` DB at teardown.
        DATABASES["default"]["CONN_MAX_AGE"] = 0
elif _RUNNING_TESTS:
    # No DATABASE_URL under pytest -> in-memory SQLite. Keeps the suite hermetic
    # (no external Postgres/Neon needed) while the app itself still requires a
    # real database, per the branch below.
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    raise ImproperlyConfigured(
        "No database configured. Neon is the platform database: set "
        "NEON_DATABASE_URL (or DATABASE_URL) to your Neon connection string, "
        "e.g. postgresql://USER:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/"
        "nlregex?sslmode=require"
    )

# --------------------------------------------------------------------------- #
# Redis — broker, result backend, and cache all live here on separate DBs.
# --------------------------------------------------------------------------- #
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

# --------------------------------------------------------------------------- #
# Spark
# --------------------------------------------------------------------------- #
# local[*]  -> bundled Spark runtime inside the worker (default; reliable).
# spark://spark-master:7077 -> standalone cluster (compose `cluster` profile).
SPARK_MASTER_URL = os.environ.get("SPARK_MASTER_URL", "local[*]")
SPARK_APP_NAME = os.environ.get("SPARK_APP_NAME", "nl-regex-engine")
# Target rows-per-partition; the engine derives a partition count from this so
# work fans out across cores/executors and progress reporting is granular.
SPARK_ROWS_PER_PARTITION = int(os.environ.get("SPARK_ROWS_PER_PARTITION", "200000"))
SPARK_SHUFFLE_PARTITIONS = int(os.environ.get("SPARK_SHUFFLE_PARTITIONS", "8"))

# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Haiku is the default: NL->regex is a small, high-volume, latency-sensitive
# task well within Haiku's capability. Override with LLM_MODEL for tougher
# descriptions (e.g. claude-sonnet-4-6 / claude-opus-4-8).
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1024"))
# A few real cell values from each target column are shown to the LLM so it can
# match the data's actual case/format (e.g. "False", not "false"). These cap the
# per-column value count and value length to keep the prompt (and token cost)
# small; sampling reads only the already-captured preview rows, never the file.
# Values are drawn spread across the preview window (not the first N rows) so
# the model sees more of the column's variety.
LLM_SAMPLE_VALUES_PER_COLUMN = int(
    os.environ.get("LLM_SAMPLE_VALUES_PER_COLUMN", "10")
)
LLM_SAMPLE_VALUE_MAXLEN = int(os.environ.get("LLM_SAMPLE_VALUE_MAXLEN", "80"))
# How long generated regexes stay cached in Redis (seconds). Default 30 days.
REGEX_CACHE_TTL = int(os.environ.get("REGEX_CACHE_TTL", str(60 * 60 * 24 * 30)))

# --------------------------------------------------------------------------- #
# Ingest limits
# --------------------------------------------------------------------------- #
UPLOAD_PREVIEW_ROWS = int(os.environ.get("UPLOAD_PREVIEW_ROWS", "20"))
# Cap on the multipart upload the web process will accept (bytes). The file is
# streamed to disk in chunks regardless; this just rejects absurd uploads early.
DATA_UPLOAD_MAX_MEMORY_SIZE = None  # we stream to disk, never buffer in memory
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))

# --------------------------------------------------------------------------- #
# DRF
# --------------------------------------------------------------------------- #
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    # No auth: this is a public, token-less API (Django's auth app is not
    # installed). Be explicit so DRF doesn't fall back to Session/Basic auth,
    # and set UNAUTHENTICATED_USER=None so DRF doesn't import
    # django.contrib.auth.models.AnonymousUser (which needs auth+contenttypes).
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "api.pagination.DefaultPagination",
    "PAGE_SIZE": 50,
}

# --------------------------------------------------------------------------- #
# CORS (dev: allow the Vite dev server / any origin)
# --------------------------------------------------------------------------- #
CORS_ALLOW_ALL_ORIGINS = _env_bool("CORS_ALLOW_ALL", True)
CORS_ALLOWED_ORIGINS = [
    o for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"}
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"}
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "processing": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "py4j": {"level": "WARNING"},
    },
}
