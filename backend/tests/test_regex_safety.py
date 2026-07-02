from processing.regex_safety import escape_replacement, validate_regex


def test_valid_pattern_passes():
    v = validate_regex(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b")
    assert v.ok, v.reason


def test_invalid_pattern_rejected():
    v = validate_regex(r"([a-z]+")  # unbalanced paren
    assert not v.ok
    assert "Invalid regex" in v.reason


def test_empty_pattern_rejected():
    assert not validate_regex("").ok
    assert not validate_regex("   ").ok


def test_nested_quantifier_rejected():
    # Classic catastrophic-backtracking shape.
    v = validate_regex(r"(a+)+$")
    assert not v.ok
    assert "backtracking" in v.reason.lower()


def test_length_cap():
    assert not validate_regex("a" * 5000).ok


def test_java_unicode_property_accepted():
    # \p{...} is valid in Java (Spark) though Python's re lacks it; it must be
    # validated via the compatible rewrite rather than rejected outright.
    assert validate_regex(r"\p{L}+\p{Digit}*").ok


def test_java_named_group_accepted():
    assert validate_regex(r"(?<word>\w+)").ok


def test_possessive_quantifier_accepted():
    # Possessive quantifiers are Java-valid and not a backtracking risk.
    assert validate_regex(r"\b\d++\b").ok


def test_escape_replacement():
    # Both backslash and dollar must be escaped for Java/Spark regexp_replace.
    assert escape_replacement("a$b\\c") == r"a\$b\\c"
    assert escape_replacement("REDACTED") == "REDACTED"
