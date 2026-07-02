"""Core Django settings: security, apps, middleware, templates, DRF, CORS.

Configuration is environment-driven so the same image runs locally, in
docker-compose, and in a deployed environment. See ``.env.example`` for the
full list of knobs.
"""
from __future__ import annotations

import os

from .env import BASE_DIR, _env_bool

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
