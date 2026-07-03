"""Natural-language -> per-column match conditions.

A prompt is resolved into a set of **predicates** — one ``{column, pattern}``
per column-condition — combined with a ``combinator`` (``all`` = AND, ``any`` =
OR). "name starts with A and phone starts with 0" becomes two predicates joined
by ``all``; a single-condition prompt over one column is a one-element list, so
this subsumes the earlier single-pattern model.

Resolution order for a prompt:

1. **Redis cache** (``processing.cache``) — identical prompts never re-hit the
   LLM. Returns ``source="cache"``.
2. **LLM** (Anthropic) when ``ANTHROPIC_API_KEY`` is set — constrained via
   structured outputs to decompose the description into per-column
   Java/Spark-compatible predicates. ``source="llm"``.
3. **Heuristic fallback** otherwise — a deterministic library covering the
   common entities (emails, phones, URLs, dates, ...), fanned across the target
   columns with ``any``, so the whole pipeline runs end-to-end with no API key.
   It can't decompose compound cross-column conditions. ``source="heuristic"``.

Whatever the source, every pattern is run through
:func:`regex_safety.validate_regex` before it is cached or returned.
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

# Concrete output actions the model may choose from (mirrors jobs.models.Job.Action
# minus `auto`). Kept as plain strings so this module stays free of a model import
# and remains unit-testable without Django's app registry.
_ACTIONS = ("find", "replace", "mask", "extract", "keep", "drop")
_DEFAULT_ACTION = "replace"
_AUTO_ACTION = "auto"

# One predicate = {column, pattern, explanation}. Shared by both schemas below so
# the two never drift.
_PREDICATE_ARRAY = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "column": {"type": "string"},
            "pattern": {"type": "string"},
            "explanation": {"type": "string"},
        },
        "required": ["column", "pattern", "explanation"],
        "additionalProperties": False,
    },
}

# `auto` path: the model must also CHOOSE the action + any inline value, so those
# fields are required in its structured output.
_CONDITIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "combinator": {"type": "string", "enum": ["all", "any"]},
        "predicates": _PREDICATE_ARRAY,
        "action": {"type": "string", "enum": list(_ACTIONS)},
        "value": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["combinator", "predicates", "action", "value", "explanation"],
    "additionalProperties": False,
}

# Explicit-action path: the action is already known, so the model only builds the
# predicates — no `action`/`value` fields to pick (or to waste tokens on).
_PREDICATES_SCHEMA = {
    "type": "object",
    "properties": {
        "combinator": {"type": "string", "enum": ["all", "any"]},
        "predicates": _PREDICATE_ARRAY,
        "explanation": {"type": "string"},
    },
    "required": ["combinator", "predicates", "explanation"],
    "additionalProperties": False,
}

# The system prompt is assembled from three parts:
#   _CORE_PROMPT            — how to decompose the description into per-column
#                             predicates + the Java-regex/safety rules. Always sent.
#   _ACTION_SELECTION_PROMPT — the verb->action menu. Sent ONLY on `auto`, where the
#                             model has to pick the action itself.
#   _ACTION_GUIDANCE[action] — for an explicit action, what its predicates' matches
#                             MEAN for that transformation, so the regex is shaped
#                             for the known action instead of generated blind to it.
# _EXPLANATION_ASK closes either tail (a one-line summary is always wanted).
_CORE_PROMPT = (
    "You convert a natural-language description of which ROWS to select into a "
    "set of per-column regex predicates. Each regex is applied by Apache Spark "
    "to a single column (rlike to test a match, regexp_replace to rewrite it), "
    "using Java regular-expression syntax (java.util.regex).\n"
    "You are given the target column names and, for each, a few sample values.\n"
    "Decompose the description into one predicate per column-condition:\n"
    "- Each predicate has a `column` that MUST be exactly one of the provided "
    "target columns, a `pattern` (the Java regex for that column), and a "
    "one-clause `explanation`.\n"
    "- `combinator` is `all` when the conditions are joined by \"and\" / must all "
    "hold, and `any` when joined by \"or\" / any may hold. For a single "
    "condition, return one predicate and use `all`.\n"
    "- If the description states ONE condition but does not say which column it "
    "applies to, emit one predicate per target column with the same pattern and "
    "set `combinator` to `any` (match if it appears in any of them).\n"
    "- When the description gives a SEPARATE condition and outcome for EACH column "
    "(e.g. \"if A is blank set it to 0, if B is blank set it to 0\", or \"redact "
    "emails in A and phones in B\"), the columns are handled independently: emit "
    "one predicate per named column and set `combinator` to `any`. Each column is "
    "then edited on its own. Reserve `all` for a single row-level condition that "
    "every column must satisfy together (e.g. \"rows where name starts with A AND "
    "phone starts with 0\").\n"
    "Pattern rules:\n"
    "- To match an EMPTY, blank, missing, or null cell, use `^\\s*$` — a "
    "missing/null value is treated as an empty string, so this fires on blank "
    "cells (used with `replace`, this fills them with the replacement value).\n"
    "- Use only constructs supported by java.util.regex: character classes, "
    "quantifiers, anchors, \\b \\d \\w \\s, and non-capturing groups (?:...).\n"
    "- The pattern is matched *within* a cell, so anchor for position: "
    "\"starts with X\" -> ^X, \"ends with X\" -> X$, \"is exactly X\" -> ^X$.\n"
    "- Do NOT use possessive quantifiers, variable-length lookbehind, or any "
    "construct that risks catastrophic backtracking. Never nest unbounded "
    "quantifiers (e.g. (a+)+). Prefer specific character classes over greedy .*.\n"
    "- Treat the sample values as ground truth for the data's actual case, "
    "spelling, and formatting: match what the samples show (e.g. if they read "
    "'False', match 'False', not 'false') unless the description explicitly "
    "asks to be case-insensitive or to normalise the value."
)

_ACTION_SELECTION_PROMPT = (
    "Also decide the `action` to perform on what the predicates select, from the "
    "verb in the description:\n"
    "- `find`: report the matches only — nothing is edited and no row is removed "
    "(find / show / highlight / count / list the matches). Choose it only when "
    "the description asks to locate matches and states no transformation.\n"
    "- `replace`: substitute the matched text. This is the default and also "
    "covers delete/remove/strip/clear (with an empty `value`).\n"
    "- `mask`: redact/hide/obscure/censor the matched text (e.g. hide card digits).\n"
    "- `extract`: keep ONLY the matched text and discard the rest of the cell "
    "(pull out / isolate / keep just the ...).\n"
    "- `keep`: keep only the ROWS that match and drop all others (filter to / "
    "keep only rows where ...).\n"
    "- `drop`: remove the ROWS that match and keep all others (delete/exclude rows "
    "where ...).\n"
    "Choose `keep`/`drop` only when the description is about whole rows; a verb "
    "like \"remove emails\" edits cells, so it is `replace` with an empty value. "
    "Default to `replace` when unsure.\n"
    "Return `value` as the literal replacement/mask text the description gives "
    "(e.g. \"replace X with REDACTED\" -> \"REDACTED\"), or \"\" when none is stated."
)

_EXPLANATION_ASK = (
    "Also return a one-sentence `explanation` of the overall row selection."
)

# Per-action regex-shaping guidance. Keyed by the concrete action; `auto` is not
# here (it gets _ACTION_SELECTION_PROMPT instead). The pattern's *meaning* differs
# by action — what it matches is edited (replace/mask), isolated (extract), or used
# as a row test (keep/drop) — so each variant steers the regex accordingly.
_ACTION_GUIDANCE = {
    "find": (
        "The action is fixed as FIND: the matched text is only reported — nothing "
        "is edited and no row is removed. Each `pattern` selects the text to "
        "locate within its column."
    ),
    "replace": (
        "The action is fixed as REPLACE: the matched text is substituted by a "
        "replacement string supplied separately (an empty one deletes it). Each "
        "`pattern` must match EXACTLY the text to be replaced — no more, no less — "
        "since whatever it matches is what gets overwritten."
    ),
    "mask": (
        "The action is fixed as MASK: the matched text is redacted in place "
        "(overwritten with a mask token). Each `pattern` must match ONLY the "
        "sensitive characters to hide, not the whole cell, so the surrounding "
        "context is preserved (e.g. match just the digits of a card number, not "
        "the label around them)."
    ),
    "extract": (
        "The action is fixed as EXTRACT: each cell is collapsed to just its "
        "matched text (regex group 0) and everything else is discarded. Each "
        "`pattern` must match EXACTLY the substring to isolate — it is NOT a "
        "whole-cell test — because whatever it matches becomes the entire kept "
        "output of that cell."
    ),
    "keep": (
        "The action is fixed as KEEP: rows whose columns satisfy the predicates "
        "are kept and all other rows dropped. Each `pattern` is a row SELECTION "
        "TEST (Spark rlike) over its column — a boolean condition, not a substring "
        "to edit. Anchor it (^/$) when the description implies the whole value."
    ),
    "drop": (
        "The action is fixed as DROP: rows whose columns satisfy the predicates "
        "are removed and all other rows kept. Each `pattern` is a row SELECTION "
        "TEST (Spark rlike) over its column — a boolean condition, not a substring "
        "to edit. Anchor it (^/$) when the description implies the whole value."
    ),
}

_AUTO_SYSTEM_PROMPT = f"{_CORE_PROMPT}\n{_ACTION_SELECTION_PROMPT}\n{_EXPLANATION_ASK}"


def _system_for_action(action: str) -> str:
    """System prompt tailored to the resolved ``action``.

    ``auto`` (or any unrecognised value) gets the full verb->action menu — the
    model has to pick the action itself. An explicit action skips that menu and
    instead receives guidance on what its matches MEAN for that transformation,
    so the regex is shaped for the known action rather than generated blind to it.
    """
    if action in _ACTION_GUIDANCE:
        return f"{_CORE_PROMPT}\n{_ACTION_GUIDANCE[action]}\n{_EXPLANATION_ASK}"
    return _AUTO_SYSTEM_PROMPT


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

# Positional phrasing around a quoted literal. Anchoring the pattern (^ / $)
# instead of the default word-boundary match is both more correct ("starts with
# 'Dr'" should not fire mid-cell) AND faster under Spark ``rlike``: an anchored
# pattern short-circuits at the cell start/end rather than scanning every offset.
_STARTS_WITH = re.compile(r"\b(?:start|starts|starting|begin|begins|beginning)\s+with\b")
_ENDS_WITH = re.compile(r"\b(?:end|ends|ending)\s+with\b")
_EXACTLY = re.compile(r"\b(?:is\s+exactly|exactly|equals?|equal\s+to)\b")


def _anchor_quoted(text: str, literal: str) -> tuple[str, str]:
    """Anchor a quoted literal per any positional phrasing in ``text`` (lowercased).

    Order matters: full-cell equality ("exactly"/"equals") is checked before the
    prefix/suffix phrasings. With no positional cue, falls back to the historical
    word-boundary match so an unqualified "the word 'cat'" is unchanged.
    """
    if _EXACTLY.search(text):
        return f"^{literal}$", "Matches values equal to the quoted text."
    if _STARTS_WITH.search(text):
        return f"^{literal}", "Matches values starting with the quoted text."
    if _ENDS_WITH.search(text):
        return f"{literal}$", "Matches values ending with the quoted text."
    return rf"\b{literal}\b", "Matches the quoted text."


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


# Verb phrasings that select a non-default action, checked in order. Row actions
# (keep/drop) require "row(s)" so a cell verb like "remove emails" is never read
# as "drop rows"; mask/extract match their characteristic verbs. `replace` is
# listed so its verbs outrank the report-only `find` when both appear ("find and
# replace X with Y"); `find` sits last for the same reason. First hit wins.
_ACTION_PHRASES: list[tuple[str, tuple[str, ...]]] = [
    ("keep", ("keep only the rows", "keep the rows", "keep only rows", "keep rows",
              "filter to", "only keep rows", "retain rows", "rows that match",
              "select rows", "keep matching rows")),
    ("drop", ("drop rows", "drop the rows", "delete rows", "delete the rows",
              "remove rows", "remove the rows", "exclude rows", "discard rows",
              "drop matching rows", "delete matching rows")),
    ("mask", ("mask", "redact", "obscure", "censor", "anonymize", "anonymise",
              "star out", "asterisk", "black out", "blank out", "hide the",
              "conceal")),
    ("extract", ("extract", "keep only the", "pull out", "isolate", "just the",
                 "only the matched", "capture only")),
    ("replace", ("replace", "substitute", "swap", "remove", "delete", "strip",
                 "clear", "change")),
    ("find", ("find", "show", "highlight", "count", "list", "locate",
              "look for", "search for")),
]

# "replace X with Y" / "... with 'Y'" — grab the literal replacement so the
# heuristic (no LLM) still honours an inline value on the default replace action.
_WITH_VALUE = re.compile(
    r"\b(?:replace|substitute|swap)\b.*?\bwith\s+(?:the\s+)?['\"]?([^'\"]{1,120}?)['\"]?\s*$",
    re.IGNORECASE,
)


def _infer_action_heuristic(prompt: str) -> tuple[str, str]:
    """Guess the output action + any inline value from the prompt, no LLM.

    Deterministic verb scan mirroring the LLM's action choice so ``auto`` still
    resolves to something sensible with no API key. Returns ``(action, value)``;
    defaults to ``("replace", "")`` when no action verb is recognised. A
    ``replace`` resolution (matched or default) still scans for an inline
    "... with Y" value.
    """
    text = prompt.lower()
    for action, phrases in _ACTION_PHRASES:
        if any(p in text for p in phrases):
            if action != _DEFAULT_ACTION:
                return action, ""
            break
    m = _WITH_VALUE.search(prompt)
    value = m.group(1).strip() if m else ""
    return _DEFAULT_ACTION, value


def _generate_with_heuristic(prompt: str) -> tuple[str, str]:
    text = prompt.lower()

    specific = _match_keywords(text, _SPECIFIC_HEURISTICS)
    if specific:
        return specific

    # A quoted literal -> anchor it per any positional phrasing, else match it as
    # a whole word.
    quoted = _QUOTED.search(prompt)
    if quoted:
        literal = re.escape(quoted.group(1))
        return _anchor_quoted(text, literal)

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


def _generate_conditions_with_llm(
    prompt: str,
    columns: Optional[list[str]],
    samples: Optional[dict[str, list[str]]] = None,
    action: str = _AUTO_ACTION,
) -> tuple[list[dict], str, str, str, str]:
    """Decompose the prompt into per-column predicates via the LLM.

    ``action`` is the requested output action. On ``auto`` the model both builds
    the predicates AND chooses the action + inline value (full menu, richer
    schema). On an explicit action the prompt is specialised to that action and
    the model only builds the predicates — the returned action is the requested
    one and the value is empty (the caller sources it from the request instead).

    Returns ``(predicates, combinator, explanation, action, value)`` where
    ``predicates`` is a list of ``{"column", "pattern", "explanation"}`` dicts.
    """
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise LLMError("anthropic SDK is not installed") from exc

    infer_action = action not in _ACTION_GUIDANCE  # auto / unrecognised -> model picks
    system = _system_for_action(action)
    schema = _CONDITIONS_SCHEMA if infer_action else _PREDICATES_SCHEMA

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    user_msg = (
        f"Description: {prompt}\n"
        f"Target column(s): {', '.join(columns) if columns else 'unspecified'}\n"
        f"{_format_samples(samples)}"
        "Return the per-column predicates and combinator that select the "
        "described rows."
    )

    try:
        response = client.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=settings.LLM_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            output_config={
                "format": {"type": "json_schema", "schema": schema}
            },
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
        predicates = data["predicates"]
        combinator = data["combinator"]
        explanation = data.get("explanation", "")
        # `action`/`value` are only in the schema on the auto path; for an explicit
        # action they were never requested, so echo the request with an empty value.
        if infer_action:
            action = data.get("action", _DEFAULT_ACTION)
            value = data.get("value", "")
        else:
            value = ""
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RegexGenerationError(
            f"LLM returned an unparseable response: {text[:200]}"
        ) from exc
    return predicates, combinator, explanation, action, value


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _context_signature(
    columns: Optional[list[str]],
    samples: Optional[dict[str, list[str]]],
    action: Optional[str] = None,
) -> str:
    """Canonical string of everything besides the prompt fed to the LLM.

    Folded into the cache key so the same words over different data — or under a
    different ``action`` — resolve to different entries. ``action`` matters now
    that an explicit action specialises the prompt: the same words under
    ``extract`` vs ``replace`` produce different predicates and must not collide.
    Column order is preserved (it is reflected in the prompt), while the sample
    mapping is serialised with sorted keys so identical content yields an
    identical signature regardless of insertion order.
    """
    if not columns and not samples and not action:
        return ""
    payload = {"columns": list(columns or []), "samples": samples or {}}
    if action:
        payload["action"] = action
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def summarize_conditions(predicates: list[dict], combinator: str) -> str:
    """A compact human-readable summary of a predicate set (for logs / the UI).

    e.g. ``Name ~ /^A/ AND phone ~ /^0/``. A single predicate renders as just
    its pattern, so single-column runs read exactly as the old regex did.
    """
    if not predicates:
        return ""
    if len(predicates) == 1:
        return predicates[0]["pattern"]
    joiner = " AND " if combinator == "all" else " OR "
    return joiner.join(f'{p["column"]} ~ /{p["pattern"]}/' for p in predicates)


def _heuristic_conditions(
    prompt: str, columns: list[str]
) -> tuple[list[dict], str, str, str, str]:
    """Build predicates without an LLM.

    The keyword heuristic maps the whole prompt to one entity pattern; it cannot
    split a compound "A and B" description into per-column conditions. So we fan
    that single pattern across every target column and combine with ``any`` —
    i.e. "the described text appears in any selected column", which is the
    behaviour the platform has always had in no-API-key mode.

    The output ``action`` (and any inline ``value``) is inferred from the prompt
    verb the same way, so ``auto`` resolves sensibly with no API key.
    """
    pattern, explanation = _generate_with_heuristic(prompt)
    action, value = _infer_action_heuristic(prompt)
    predicates = [
        {"column": col, "pattern": pattern, "explanation": explanation}
        for col in columns
    ]
    return predicates, "any", explanation, action, value


def generate_conditions(
    prompt: str,
    columns: Optional[list[str]] = None,
    samples: Optional[dict[str, list[str]]] = None,
    action: str = _AUTO_ACTION,
) -> dict:
    """Resolve a prompt to a validated set of per-column match predicates.

    A row is selected when its columns satisfy the returned ``predicates``
    combined with ``combinator`` (``all`` = AND, ``any`` = OR). Each predicate
    is ``{"column", "pattern", "explanation"}`` and its ``column`` is always one
    of ``columns``.

    ``samples`` maps a target column to a few of its real cell values; passing
    them lets the LLM match the data's actual case/format. They are only used on
    the LLM path — the heuristic is deterministic and data-independent, so it
    keeps prompt-only caching.

    ``action`` is the requested output action. On ``auto`` the model infers it
    from the prompt verb; on an explicit action the LLM prompt is specialised to
    that transformation so the regex is shaped for it (e.g. an ``extract`` pattern
    isolates a substring, a ``keep`` pattern is a row test). It is folded into the
    LLM cache key so the same words under different actions don't collide.

    Returns ``{"predicates", "combinator", "explanation", "action", "value",
    "source"}``. ``action`` is the resolved output action (one of ``_ACTIONS``)
    and ``value`` any inline replacement/mask text — the caller decides whether
    to honour them (only when the request's action is ``auto``). Raises
    :class:`LLMError` (transient/retryable), :class:`RegexGenerationError`, or
    :class:`UnsafeRegexError` (both permanent).
    """
    columns = list(columns or [])
    if not columns:
        raise RegexGenerationError(
            "At least one target column is required to build a match condition."
        )

    use_llm = bool(settings.ANTHROPIC_API_KEY)
    model = settings.LLM_MODEL if use_llm else _HEURISTIC_MODEL_TAG
    context = _context_signature(columns, samples, action) if use_llm else ""

    cached = cache.get_cached_regex(prompt, model, context)
    if cached and cached.get("predicates"):
        logger.info("Regex cache hit for prompt=%r", prompt[:80])
        # Entries cached before actions existed lack these keys — default them so
        # every return shape carries an action/value.
        cached.setdefault("action", _DEFAULT_ACTION)
        cached.setdefault("value", "")
        return {**cached, "source": "cache"}

    if use_llm:
        predicates, combinator, explanation, resolved_action, value = (
            _generate_conditions_with_llm(prompt, columns, samples, action)
        )
        source = "llm"
    else:
        predicates, combinator, explanation, resolved_action, value = (
            _heuristic_conditions(prompt, columns)
        )
        source = "heuristic"

    predicates, combinator = _validate_conditions(predicates, combinator, columns)
    resolved_action = resolved_action if resolved_action in _ACTIONS else _DEFAULT_ACTION

    payload = {
        "predicates": predicates,
        "combinator": combinator,
        "explanation": explanation,
        "action": resolved_action,
        "value": value or "",
    }
    cache.set_cached_regex(prompt, model, payload, context)
    logger.info(
        "Generated %d predicate(s) via %s: %s",
        len(predicates), source, summarize_conditions(predicates, combinator),
    )
    return {**payload, "source": source}


def _validate_conditions(
    predicates: list[dict], combinator: str, columns: list[str]
) -> tuple[list[dict], str]:
    """Sanity-check + safety-check a resolved predicate set.

    Ensures at least one predicate, that every ``column`` is a real target
    column, and that every ``pattern`` passes the ReDoS validator before it can
    reach Spark. Returns the cleaned ``(predicates, combinator)``.
    """
    if not predicates:
        raise RegexGenerationError(
            "Could not derive any match condition from the description."
        )

    available = set(columns)
    cleaned: list[dict] = []
    for p in predicates:
        column = p.get("column")
        pattern = p.get("pattern")
        if column not in available:
            raise RegexGenerationError(
                f"Condition references column {column!r}, which is not one of "
                f"the selected columns: {', '.join(columns)}."
            )
        validation = validate_regex(pattern)
        if not validation.ok:
            raise UnsafeRegexError(validation.reason)
        cleaned.append({
            "column": column,
            "pattern": pattern,
            "explanation": p.get("explanation", ""),
        })

    combinator = combinator if combinator in ("all", "any") else "all"
    return cleaned, combinator
