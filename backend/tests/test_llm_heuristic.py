import pytest

from processing import cache, llm
from processing.exceptions import RegexGenerationError


@pytest.fixture(autouse=True)
def no_cache_no_key(settings, monkeypatch):
    # Force the heuristic path and bypass Redis so the tests are hermetic.
    settings.ANTHROPIC_API_KEY = ""
    monkeypatch.setattr(cache, "get_cached_regex", lambda *a, **k: None)
    monkeypatch.setattr(cache, "set_cached_regex", lambda *a, **k: None)


def test_email_heuristic():
    out = llm.generate_regex("Find email addresses in the Email column")
    assert out["source"] == "heuristic"
    assert "@" in out["pattern"]


def test_phone_heuristic():
    out = llm.generate_regex("redact phone numbers")
    assert out["source"] == "heuristic"
    assert r"\d" in out["pattern"]


def test_quoted_literal():
    out = llm.generate_regex("replace the word 'cat'")
    assert out["pattern"] == r"\bcat\b"


def test_unknown_prompt_raises():
    with pytest.raises(RegexGenerationError):
        llm.generate_regex("do something clever to the data zzz")


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
    out = llm.generate_regex(prompt)
    assert out["source"] == "heuristic"
    assert needle in out["pattern"]


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
        llm.generate_regex(prompt)


def test_explanation_has_no_implementation_tag():
    out = llm.generate_regex("find email addresses")
    assert "heuristic" not in out["explanation"].lower()


def test_generated_pattern_is_safe():
    # Whatever the heuristic returns must pass the safety validator.
    from processing.regex_safety import validate_regex

    out = llm.generate_regex("find urls")
    assert validate_regex(out["pattern"]).ok
