"""Natural-language -> regex conversion.

Resolution order for a prompt:

1. **Redis cache** (``processing.cache``) — identical prompts never re-hit the
   LLM. Returns ``source="cache"``.
2. **LLM** (Anthropic) when ``ANTHROPIC_API_KEY`` is set — constrained to emit a
   single Java/Spark-compatible regex via structured outputs. ``source="llm"``.
3. **Heuristic fallback** otherwise — a deterministic library covering the
   common entities (emails, phones, URLs, dates, ...), so the whole pipeline
   runs end-to-end with no API key. ``source="heuristic"``.

Whatever the source, the pattern is run through :func:`regex_safety.validate_regex`
before it is cached or returned.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from django.conf import settings

from . import cache
from .exceptions import LLMError, RegexGenerationError, UnsafeRegexError
from .regex_safety import validate_regex

logger = logging.getLogger("processing")

_HEURISTIC_MODEL_TAG = "heuristic:v1"

_REGEX_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["pattern", "explanation"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You convert a natural-language description into a single regular "
    "expression. The regex is applied by Apache Spark's regexp_replace, which "
    "uses Java regular-expression syntax (java.util.regex).\n"
    "Rules:\n"
    "- Output only constructs supported by java.util.regex: character classes, "
    "quantifiers, anchors, \\b \\d \\w \\s, and non-capturing groups (?:...).\n"
    "- Do NOT use possessive quantifiers, variable-length lookbehind, or any "
    "construct that risks catastrophic backtracking. Never nest unbounded "
    "quantifiers (e.g. (a+)+).\n"
    "- Prefer specific character classes over greedy .*.\n"
    "- The pattern should match the described substrings *within* a text cell.\n"
    "- Sample values from the target column(s) may be provided. Treat them as "
    "ground truth for the data's actual case, spelling, and formatting: match "
    "what the samples show (e.g. if they read 'False', match 'False', not "
    "'false') unless the description explicitly asks to be case-insensitive or "
    "to normalise the value.\n"
    "Return the regex and a one-sentence explanation."
)


# --------------------------------------------------------------------------- #
# Heuristic fallback library
# --------------------------------------------------------------------------- #
# (keywords, pattern, explanation). First match wins. Resolution order is:
# specific entities -> quoted literal -> generic catch-alls, so a prompt like
# "replace the word 'cat'" matches the literal rather than the generic "word"
# rule. Keyword lists are intentionally broad (synonyms, plurals, common
# phrasings) so everyday descriptions resolve instead of failing.
_SPECIFIC_HEURISTICS: list[tuple[tuple[str, ...], str, str]] = [
    (("email", "e-mail", "mail address"),
     r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b",
     "Matches email addresses."),
    (("ipv4", "ip address", "ip addr"),
     r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "Matches IPv4 addresses."),
    (("url", "link", "website", "http", "hyperlink", "web address", "domain"),
     r"https?://[^\s]+|\bwww\.[^\s]+", "Matches web addresses."),
    (("ssn", "social security"),
     r"\b\d{3}-\d{2}-\d{4}\b", "Matches US Social Security numbers."),
    (("credit card", "card number", "debit card", "card no"),
     r"\b(?:\d[ -]?){13,16}\b", "Matches 13-16 digit card numbers."),
    (("phone", "telephone", "mobile", "cell", "fax", "contact number"),
     r"\+?\d{0,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}",
     "Matches phone numbers."),
    (("zip", "postal code", "postcode", "post code"),
     r"\b\d{5}(?:-\d{4})?\b", "Matches US ZIP / postal codes."),
    (("uuid", "guid"),
     r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b",
     "Matches UUIDs."),
    (("mac address",),
     r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b", "Matches MAC addresses."),
    (("hex color", "hex colour", "color code", "colour code", "hex code"),
     r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", "Matches hex color codes."),
    (("percent", "percentage"),
     r"\d+(?:\.\d+)?\s?%", "Matches percentages."),
    (("currency", "dollar", "price", "money", "amount", "cost", "payment",
      "usd", "$", "£", "€"),
     r"[$£€]\s?\d+(?:,\d{3})*(?:\.\d{2})?", "Matches currency amounts."),
    (("date", "birthday", "date of birth", "dob"),
     r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b",
     "Matches ISO or slash-style dates."),
    (("time", "timestamp", "clock"),
     r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "Matches HH:MM(:SS) times."),
    (("hashtag",), r"#\w+", "Matches hashtags."),
    (("mention", "handle", "username", "@-mention"),
     r"@\w+", "Matches @-mentions."),
    (("html tag", "html element", "markup", "html"),
     r"</?[A-Za-z][^>]*>", "Matches HTML tags."),
    (("whitespace", "extra space", "extra spaces", "trailing space", "tabs"),
     r"\s+", "Matches runs of whitespace."),
]

_GENERIC_HEURISTICS: list[tuple[tuple[str, ...], str, str]] = [
    (("digit", "number", "numeric", "integer", "decimal", "figure"),
     r"-?\d+(?:\.\d+)?", "Matches numbers."),
    (("alphanumeric",), r"[A-Za-z0-9]+", "Matches alphanumeric runs."),
    (("uppercase", "capital letter", "capitals"),
     r"[A-Z]+", "Matches uppercase letter runs."),
    (("lowercase",), r"[a-z]+", "Matches lowercase letter runs."),
    (("letter", "alphabetic", "alpha"), r"[A-Za-z]+", "Matches letters."),
    (("punctuation", "symbol"), r"[^\w\s]+", "Matches punctuation / symbols."),
    (("word",), r"\b\w+\b", "Matches words."),
]

_QUOTED = re.compile(r"""['"]([^'"]{1,200})['"]""")


def _keyword_present(text: str, keyword: str) -> bool:
    """True if ``keyword`` appears in ``text`` (already lower-cased).

    Word-keywords match on a left word boundary so plurals/inflections are
    caught ("email" -> "emails") without mid-word false positives ("ip" must
    not fire on "description" or "recipient", "date" must not fire on
    "update"). Symbol keywords ($, %, ...) fall back to plain containment.
    """
    if keyword[:1].isalnum():
        return re.search(r"(?<![a-z0-9])" + re.escape(keyword), text) is not None
    return keyword in text


def _match_keywords(text: str, table) -> tuple[str, str] | None:
    for keywords, pattern, explanation in table:
        if any(_keyword_present(text, kw) for kw in keywords):
            return pattern, explanation
    return None


def _generate_with_heuristic(prompt: str) -> tuple[str, str]:
    text = prompt.lower()

    specific = _match_keywords(text, _SPECIFIC_HEURISTICS)
    if specific:
        return specific

    # A quoted literal -> match that literal as a whole word.
    quoted = _QUOTED.search(prompt)
    if quoted:
        literal = re.escape(quoted.group(1))
        return rf"\b{literal}\b", "Matches the quoted text."

    generic = _match_keywords(text, _GENERIC_HEURISTICS)
    if generic:
        return generic

    raise RegexGenerationError(
        "Could not derive a regex from the description. Set ANTHROPIC_API_KEY "
        "to enable LLM-based generation, or describe a known entity "
        "(email, phone, url, date, number, ...) or quote the literal text."
    )


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #
def _format_samples(samples: Optional[dict[str, list[str]]]) -> str:
    """Render sampled column values as a compact block for the user message.

    Empty/absent samples produce an empty string so the message is unchanged
    from the pre-sampling behaviour.
    """
    if not samples:
        return ""
    lines = [
        "Sample values from the target column(s) (match their exact case and "
        "formatting):"
    ]
    for col, values in samples.items():
        # json.dumps quotes each value and escapes control chars while leaving
        # non-ASCII (accented/CJK) text readable for the model.
        rendered = ", ".join(json.dumps(v, ensure_ascii=False) for v in values)
        lines.append(f"- {col}: {rendered}")
    return "\n".join(lines) + "\n"


def _generate_with_llm(
    prompt: str,
    columns: Optional[list[str]],
    samples: Optional[dict[str, list[str]]] = None,
) -> tuple[str, str]:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise LLMError("anthropic SDK is not installed") from exc

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    user_msg = (
        f"Description: {prompt}\n"
        f"Target column(s): {', '.join(columns) if columns else 'unspecified'}\n"
        f"{_format_samples(samples)}"
        "Return the regex that finds the described text."
    )

    try:
        response = client.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=settings.LLM_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_config={"format": {"type": "json_schema", "schema": _REGEX_SCHEMA}},
        )
    except anthropic.APIConnectionError as exc:
        raise LLMError(f"LLM connection error: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError(f"LLM rate limited: {exc}") from exc
    except anthropic.APIStatusError as exc:
        # 5xx is transient (retry); 4xx is a permanent problem with the request.
        if exc.status_code >= 500:
            raise LLMError(f"LLM server error: {exc}") from exc
        raise RegexGenerationError(f"LLM rejected the request: {exc}") from exc

    # Structured outputs guarantee a single JSON text block on a normal stop;
    # a safety refusal returns no usable pattern (permanent, don't retry).
    if getattr(response, "stop_reason", None) == "refusal":
        raise RegexGenerationError(
            "The LLM declined to generate a pattern for this description."
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
        pattern = data["pattern"]
        explanation = data.get("explanation", "")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RegexGenerationError(
            f"LLM returned an unparseable response: {text[:200]}"
        ) from exc
    return pattern, explanation


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _context_signature(
    columns: Optional[list[str]], samples: Optional[dict[str, list[str]]]
) -> str:
    """Canonical string of everything besides the prompt fed to the LLM.

    Folded into the cache key so the same words over different data resolve to
    different entries. Column order is preserved (it is reflected in the prompt),
    while the sample mapping is serialised with sorted keys so identical content
    yields an identical signature regardless of insertion order.
    """
    if not columns and not samples:
        return ""
    return json.dumps(
        {"columns": list(columns or []), "samples": samples or {}},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def generate_regex(
    prompt: str,
    columns: Optional[list[str]] = None,
    samples: Optional[dict[str, list[str]]] = None,
) -> dict:
    """Resolve a prompt to a validated regex.

    ``samples`` maps a target column to a few of its real cell values; passing
    them lets the LLM match the data's actual case/format. They are only used on
    the LLM path — the heuristic is deterministic and data-independent, so it
    keeps prompt-only caching.

    Returns ``{"pattern", "explanation", "source"}``. Raises
    :class:`LLMError` (transient/retryable), :class:`RegexGenerationError`, or
    :class:`UnsafeRegexError` (both permanent).
    """
    use_llm = bool(settings.ANTHROPIC_API_KEY)
    model = settings.LLM_MODEL if use_llm else _HEURISTIC_MODEL_TAG
    context = _context_signature(columns, samples) if use_llm else ""

    cached = cache.get_cached_regex(prompt, model, context)
    if cached and cached.get("pattern"):
        logger.info("Regex cache hit for prompt=%r", prompt[:80])
        return {**cached, "source": "cache"}

    if use_llm:
        pattern, explanation = _generate_with_llm(prompt, columns, samples)
        source = "llm"
    else:
        pattern, explanation = _generate_with_heuristic(prompt)
        source = "heuristic"

    validation = validate_regex(pattern)
    if not validation.ok:
        raise UnsafeRegexError(validation.reason)

    payload = {"pattern": pattern, "explanation": explanation}
    cache.set_cached_regex(prompt, model, payload, context)
    logger.info("Generated regex via %s: %s", source, pattern)
    return {**payload, "source": source}
