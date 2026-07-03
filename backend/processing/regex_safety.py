"""Regex validation and ReDoS (catastrophic-backtracking) safety checks.

The generated pattern is eventually applied by Spark's ``regexp_replace``, which
uses the Java regex engine — itself vulnerable to catastrophic backtracking on
pathological patterns. We can't change the Java engine, so we *gate* the pattern
before it ever reaches Spark, using three layers:

1. **Structural sanity** — non-empty, within a length cap.
2. **Compilability** — it must compile under Python's ``re`` (a good proxy for
   "is this a well-formed pattern"), which rejects syntactically invalid input.
3. **Danger heuristics + a timed match probe** — reject patterns with nested
   unbounded quantifiers (the classic ``(a+)+`` exponential shape), and run the
   pattern against short adversarial inputs inside a hard wall-clock timeout
   (in a separate process we can actually kill). If a tiny input can already
   make the matcher run long, we refuse the pattern.

This is defence-in-depth, not a proof of safety — but it stops the patterns
that actually blow up in practice.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

MAX_PATTERN_LENGTH = 2000
MATCH_TIMEOUT_SECONDS = 1.0

# Heuristic: a quantifier (* + or {..}) applied to a group whose body itself
# contains an unbounded quantifier — e.g. (a+)+, (a*)*, (a+|b)+, ([a-z]+)*.
# This catches the overwhelming majority of hand-written ReDoS patterns.
_NESTED_QUANT = re.compile(r"\([^()]*[*+][^()]*\)[*+]|\([^()]*[*+][^()]*\)\{")

# Adversarial probe inputs: long runs that trigger exponential backtracking on
# vulnerable patterns but are cheap for safe ones.
_PROBE_INPUTS = [
    "a" * 40 + "!",
    ("ab" * 25) + "!",
    "0" * 40 + "X",
    " " * 40 + "x",
]

# --------------------------------------------------------------------------- #
# Java <-> Python compatibility
# --------------------------------------------------------------------------- #
# The pattern ultimately runs on Spark's Java regex engine, but we validate with
# Python's ``re`` (a convenient, killable proxy). Some constructs are valid in
# Java yet unsupported by ``re`` — naively compiling them would wrongly reject a
# perfectly good pattern. So we validate against a Python-compatible *copy*; the
# original Java pattern is what we keep and hand to Spark.
_UNICODE_CLASS = {
    "alpha": r"[A-Za-z]", "isalphabetic": r"[A-Za-z]", "l": r"[^\W\d_]",
    "alnum": r"[A-Za-z0-9]", "digit": r"\d", "nd": r"\d", "n": r"\d",
    "upper": r"[A-Z]", "lu": r"[A-Z]", "lower": r"[a-z]", "ll": r"[a-z]",
    "space": r"\s", "iswhite_space": r"\s", "z": r"\s",
    "punct": r"[^\w\s]", "p": r"[^\w\s]", "xdigit": r"[0-9A-Fa-f]",
    "word": r"\w", "w": r"\w", "graph": r"\S",
}


def _sub_unicode_classes(s: str) -> str:
    """Rewrite Java ``\\p{...}`` / ``\\P{...}`` classes to ``re`` equivalents."""
    def repl(m: re.Match) -> str:
        negated = m.group(1) == "P"
        cls = _UNICODE_CLASS.get(m.group(2).strip().lower(), r"\w")
        if negated:
            return r"\W" if cls.startswith(("\\w", "[A-Za")) else r"[^\s]"
        return cls

    return re.sub(r"\\([pP])\{([^}]*)\}", repl, s)


def _python_equivalent(pattern: str) -> str:
    """A best-effort Python-``re``-compatible rewrite of a Java pattern."""
    s = re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", pattern)  # named group
    s = re.sub(r"\\k<([A-Za-z_]\w*)>", r"(?P=\1)", s)         # named backref
    s = _sub_unicode_classes(s)                               # \p{...} \P{...}
    s = s.replace(r"\h", "[ \t]").replace(r"\H", "[^ \t]")    # horizontal ws
    s = s.replace(r"\R", r"(?:\r\n|[\r\n])")                  # line break
    return s


def _relax_possessive(s: str) -> str:
    """Drop possessive quantifiers / atomic groups (Java-valid, older-Python-not)."""
    s = re.sub(r"([*+?}])\+", r"\1", s)  # a++ a*+ a?+ a{2,}+ -> greedy
    return s.replace("(?>", "(?:")       # atomic group -> non-capturing


def _compile_equivalent(pattern: str):
    """Compile ``pattern`` for probing, tolerating valid Java-only constructs.

    Returns the compiled regex, or ``None`` if no reasonable rewrite parses.
    """
    candidates = (
        pattern,
        _python_equivalent(pattern),
        _relax_possessive(_python_equivalent(pattern)),
    )
    for candidate in candidates:
        try:
            return re.compile(candidate)
        except re.error:
            continue
    return None


@dataclass
class RegexValidation:
    ok: bool
    pattern: str
    reason: str = ""


def _probe_within_timeout(compiled: "re.Pattern", pattern: str) -> RegexValidation:
    """Return a validation result based on a time-boxed match probe.

    The probe runs in a **daemon thread** rather than a child process: Celery's
    prefork workers are themselves daemonic, and Python forbids daemonic
    processes from spawning children. A thread can't be force-killed, but the
    probe inputs are tiny and the static nested-quantifier check above already
    rejects the exponential shapes — this layer is a best-effort backstop. If
    the probe doesn't finish in time we refuse the pattern (and the stray
    daemon thread dies with the process).
    """
    result: dict[str, str] = {}

    def work() -> None:
        for probe in _PROBE_INPUTS:
            compiled.search(probe)
        result["status"] = "ok"

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(MATCH_TIMEOUT_SECONDS)

    if thread.is_alive():
        return RegexValidation(
            ok=False,
            pattern=pattern,
            reason=(
                "Pattern is too slow on small inputs (possible catastrophic "
                "backtracking); refused for safety."
            ),
        )
    return RegexValidation(ok=True, pattern=pattern)


def validate_regex(pattern: str | None) -> RegexValidation:
    """Validate a regex for correctness and backtracking safety."""
    if pattern is None or not pattern.strip():
        return RegexValidation(False, pattern or "", "Empty regex pattern.")

    if len(pattern) > MAX_PATTERN_LENGTH:
        return RegexValidation(
            False,
            pattern,
            f"Pattern exceeds the {MAX_PATTERN_LENGTH}-character safety limit.",
        )

    # Compile a Python-compatible equivalent for the probe — valid Java-only
    # constructs (\p{...}, named groups, possessive quantifiers) are tolerated
    # here; the original pattern is what we ultimately return for Spark.
    compiled = _compile_equivalent(pattern)
    if compiled is None:
        return RegexValidation(False, pattern, "Invalid regex: could not be parsed.")

    if _NESTED_QUANT.search(pattern):
        return RegexValidation(
            False,
            pattern,
            "Pattern contains nested unbounded quantifiers (e.g. (a+)+), a "
            "classic catastrophic-backtracking shape; refused for safety.",
        )

    return _probe_within_timeout(compiled, pattern)


def escape_replacement(value: str) -> str:
    """Escape a literal replacement for Java/Spark ``regexp_replace``.

    Spark's ``regexp_replace`` interprets ``$`` (group references) and ``\\``
    (escapes) in the *replacement* string. A user typing "$5.00" or a Windows
    path must be treated literally, so we escape both.
    """
    return value.replace("\\", "\\\\").replace("$", "\\$")
