"""LLM."""
from __future__ import annotations

import os

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
