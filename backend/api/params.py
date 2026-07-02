"""Small helpers for parsing query/request parameters shared across views."""
from __future__ import annotations


def _truthy(value: str | None) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}
