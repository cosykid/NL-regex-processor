import pytest

from processing import cache, llm
from processing.exceptions import RegexGenerationError


@pytest.fixture(autouse=True)
def no_cache_no_key(settings, monkeypatch):
    # Force the heuristic path and bypass Redis so the tests are hermetic.
    settings.ANTHROPIC_API_KEY = ""
    monkeypatch.setattr(cache, "get_cached_regex", lambda *a, **k: None)
    monkeypatch.setattr(cache, "set_cached_regex", lambda *a, **k: None)


def _pattern(out) -> str:
    """The pattern of the first predicate (heuristic emits one per column)."""
    return out["predicates"][0]["pattern"]


def test_email_heuristic():
    out = llm.generate_conditions(
        "Find email addresses in the Email column", ["Email"]
    )
    assert out["source"] == "heuristic"
    assert "@" in _pattern(out)


def test_phone_heuristic():
    out = llm.generate_conditions("redact phone numbers", ["Phone"])
    assert out["source"] == "heuristic"
    assert r"\d" in _pattern(out)


def test_quoted_literal():
    out = llm.generate_conditions("replace the word 'cat'", ["Notes"])
    assert _pattern(out) == r"\bcat\b"


def test_quoted_literal_starts_with_is_anchored():
    # Positional phrasing anchors the literal to the cell start (^) — both more
    # correct and cheaper for Spark rlike than a scan-every-offset \b match.
    out = llm.generate_conditions("rows where name starts with 'Dr'", ["name"])
    assert _pattern(out) == r"^Dr"


def test_quoted_literal_ends_with_is_anchored():
    out = llm.generate_conditions("names ending with 'son'", ["name"])
    assert _pattern(out) == r"son$"


def test_quoted_literal_exactly_is_fully_anchored():
    out = llm.generate_conditions("rows where status is exactly 'Yes'", ["status"])
    assert _pattern(out) == r"^Yes$"


def test_unknown_prompt_raises():
    with pytest.raises(RegexGenerationError):
        llm.generate_conditions("do something clever to the data zzz", ["col"])


def test_no_columns_raises():
    # Every match condition needs a column to attach to.
    with pytest.raises(RegexGenerationError):
        llm.generate_conditions("find emails", [])


def test_heuristic_fans_pattern_across_columns():
    # Without an LLM the heuristic can't split a compound description, so it
    # applies the one entity pattern to every selected column and ORs them.
    out = llm.generate_conditions("find email addresses", ["Work", "Personal"])
    assert out["combinator"] == "any"
    assert [p["column"] for p in out["predicates"]] == ["Work", "Personal"]
    assert all("@" in p["pattern"] for p in out["predicates"])


@pytest.mark.parametrize(
    "prompt, needle",
    [
        ("redact e-mail addresses", "@"),       # hyphenated synonym
        ("strip percentages", "%"),
        ("find numbers", r"\d"),
        ("extract uuids", "-"),
        ("remove extra whitespace", r"\s"),
        ("find hex color codes", "#"),
        ("match the timestamp", ":"),
    ],
)
def test_expanded_heuristic_coverage(prompt, needle):
    out = llm.generate_conditions(prompt, ["col"])
    assert out["source"] == "heuristic"
    assert needle in _pattern(out)


@pytest.mark.parametrize(
    "prompt",
    [
        "update the contact info",  # "update" must NOT trigger the date rule
        "the recipient list",       # "recipient" must NOT trigger the ip rule
    ],
)
def test_no_midword_false_positives(prompt):
    # These describe no known entity, so generation should fail rather than
    # silently match a rule off a mid-word substring.
    with pytest.raises(RegexGenerationError):
        llm.generate_conditions(prompt, ["col"])


def test_explanation_has_no_implementation_tag():
    out = llm.generate_conditions("find email addresses", ["Email"])
    assert "heuristic" not in out["explanation"].lower()


def test_generated_pattern_is_safe():
    # Whatever the heuristic returns must pass the safety validator.
    from processing.regex_safety import validate_regex

    out = llm.generate_conditions("find urls", ["Site"])
    assert validate_regex(_pattern(out)).ok


# --------------------------------------------------------------------------- #
# Output-action inference (no-API-key path)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "prompt, action",
    [
        ("redact credit card numbers", "mask"),
        ("mask the phone numbers", "mask"),
        ("obscure email addresses", "mask"),
        ("extract email addresses", "extract"),
        ("pull out the dates", "extract"),
        ("keep only rows where email contains example", "keep"),
        ("filter to rows with a phone number", "keep"),
        ("drop rows where the email is empty", "drop"),
        ("delete rows with test emails", "drop"),
        ("remove email addresses", "replace"),   # cell edit, NOT a row drop
        ("find phone numbers", "find"),           # report-only, nothing changes
        ("show rows where email is empty", "find"),
        ("highlight the dates", "find"),
        ("find and replace emails with X", "replace"),  # transform verb outranks find
        ("email addresses", "replace"),           # no verb -> default
    ],
)
def test_heuristic_infers_action(prompt, action):
    a, _ = llm._infer_action_heuristic(prompt)
    assert a == action


def test_heuristic_pulls_inline_replace_value():
    # "replace X with Y" -> value carried on the default replace action.
    a, value = llm._infer_action_heuristic("replace phone numbers with N/A")
    assert a == "replace"
    assert value == "N/A"


def test_generate_conditions_surfaces_action_and_value():
    out = llm.generate_conditions("redact the email addresses", ["Email"])
    assert out["action"] == "mask"
    assert out["value"] == ""  # heuristic doesn't invent a mask token
