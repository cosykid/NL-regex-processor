"""Database — Neon is the single source of truth.

Jobs (status, progress, resolved regex, result metadata) are persisted to a
managed Postgres / Neon instance resolved from NEON_DATABASE_URL / DATABASE_URL.
There is intentionally **no** SQLite or local-Postgres fallback for the app:
DATABASE_URL is required and the app refuses to start without it. The one
exception is the test suite — when running under pytest with no DATABASE_URL,
an in-memory SQLite is used so the hermetic suite needs no external database.
"""
from __future__ import annotations

import os
import sys

from django.core.exceptions import ImproperlyConfigured


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
