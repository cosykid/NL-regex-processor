"""Redis-backed regex cache and job-cancellation flags.

Per the spec, identical natural-language prompts must not be re-sent to the LLM.
We key the cache on a normalised hash of the prompt (+ model + context), so the
same request — from any user — reuses the previously generated, already-validated
regex.

``context`` is a canonical signature of everything *besides* the prompt that is
fed to the LLM: the target columns and a few sample cell values from each. It is
part of the key because the same words can legitimately need a different regex
depending on the data — "remove False" over a column of ``False``/``True`` is not
the same request as over a column of ``FALSE``. Without it, the first dataset's
result would be cached and wrongly served to every later same-prompt request.
The deterministic heuristic ignores the data, so it passes ``context=""`` and
keeps prompt-only caching.

This module talks to Redis directly (not Django's cache framework) so the
cancellation flags and the regex cache share one well-documented connection,
and so a Redis outage degrades gracefully (cache miss) instead of 500-ing.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

import redis
from django.conf import settings

logger = logging.getLogger("processing")

_client: Optional[redis.Redis] = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.REDIS_CACHE_URL, decode_responses=True
        )
    return _client


# --------------------------------------------------------------------------- #
# Regex cache
# --------------------------------------------------------------------------- #
def regex_cache_key(prompt: str, model: str, context: str = "") -> str:
    normalised = " ".join(prompt.lower().split())
    digest = hashlib.sha256(
        f"{model}::{normalised}::{context}".encode()
    ).hexdigest()
    # v2: the key now folds in the data context (see module docstring). Bumping
    # the version retires v1 entries, which were keyed on prompt+model only.
    return f"regex:v2:{digest}"


def get_cached_regex(prompt: str, model: str, context: str = "") -> Optional[dict]:
    """Return a cached ``{"pattern", "explanation", ...}`` dict, or ``None``."""
    try:
        raw = get_client().get(regex_cache_key(prompt, model, context))
    except redis.RedisError as exc:  # pragma: no cover - degrade gracefully
        logger.warning("Regex cache read failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:  # pragma: no cover - corrupt entry
        return None


def set_cached_regex(
    prompt: str, model: str, payload: dict, context: str = ""
) -> None:
    try:
        get_client().set(
            regex_cache_key(prompt, model, context),
            json.dumps(payload),
            ex=settings.REGEX_CACHE_TTL,
        )
    except redis.RedisError as exc:  # pragma: no cover - degrade gracefully
        logger.warning("Regex cache write failed: %s", exc)


# --------------------------------------------------------------------------- #
# Cancellation flags
# --------------------------------------------------------------------------- #
def _cancel_key(job_id) -> str:
    return f"job:cancel:{job_id}"


def request_cancel(job_id) -> None:
    """Mark a job as cancel-requested. The running task polls this flag."""
    try:
        get_client().set(_cancel_key(job_id), "1", ex=60 * 60)
    except redis.RedisError as exc:  # pragma: no cover
        logger.warning("Cancel flag write failed: %s", exc)


def is_cancelled(job_id) -> bool:
    try:
        return bool(get_client().exists(_cancel_key(job_id)))
    except redis.RedisError:  # pragma: no cover
        return False


def clear_cancel(job_id) -> None:
    try:
        get_client().delete(_cancel_key(job_id))
    except redis.RedisError:  # pragma: no cover
        pass
