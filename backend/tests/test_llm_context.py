"""Data-sample context: the LLM sees real column values, and the regex cache is
scoped to that data so one dataset's result can't be served to another."""
import pytest

from processing import cache, llm
from processing.exceptions import RegexGenerationError


# --------------------------------------------------------------------------- #
# Rendering the sample block into the user message
# --------------------------------------------------------------------------- #
def test_format_samples_preserves_case_and_quotes():
    block = llm._format_samples({"Railway": ["False", "True"]})
    # Exact case is preserved (the whole point) and values are quoted so the
    # model sees their boundaries.
    assert '"False"' in block and '"True"' in block
    assert "Railway" in block


def test_format_samples_empty_is_blank():
    assert llm._format_samples(None) == ""
    assert llm._format_samples({}) == ""


# --------------------------------------------------------------------------- #
# Predicate-set summary (human-readable, for logs + the UI pattern strip)
# --------------------------------------------------------------------------- #
def test_summarize_single_predicate_is_just_the_pattern():
    out = llm.summarize_conditions(
        [{"column": "Name", "pattern": "^A", "explanation": ""}], "all"
    )
    assert out == "^A"


def test_summarize_multi_predicate_joins_with_combinator():
    preds = [
        {"column": "name", "pattern": "^A", "explanation": ""},
        {"column": "phone", "pattern": "^0", "explanation": ""},
    ]
    anded = llm.summarize_conditions(preds, "all")
    assert "AND" in anded and "name" in anded and "phone" in anded
    ored = llm.summarize_conditions(preds, "any")
    assert "OR" in ored


# --------------------------------------------------------------------------- #
# Cache-key context signature
# --------------------------------------------------------------------------- #
def test_context_signature_distinguishes_data():
    same = llm._context_signature(["Railway"], {"Railway": ["False"]})
    other = llm._context_signature(["Railway"], {"Railway": ["FALSE"]})
    assert same != other
    # Stable for identical inputs (so identical requests share a cache entry).
    assert same == llm._context_signature(["Railway"], {"Railway": ["False"]})


def test_context_signature_empty_when_no_context():
    assert llm._context_signature(None, None) == ""
    assert llm._context_signature([], {}) == ""


def test_cache_key_folds_in_context():
    base = cache.regex_cache_key("remove False", "m")
    with_ctx = cache.regex_cache_key("remove False", "m", "ctx")
    assert base != with_ctx
    assert with_ctx.startswith("regex:v4:")


# --------------------------------------------------------------------------- #
# End-to-end wiring through generate_conditions (LLM path, no network)
# --------------------------------------------------------------------------- #
@pytest.fixture
def in_memory_cache(monkeypatch):
    """A dict-backed stand-in for Redis, keyed exactly like the real cache."""
    store: dict[str, dict] = {}

    def fake_get(prompt, model, context=""):
        return store.get(cache.regex_cache_key(prompt, model, context))

    def fake_set(prompt, model, payload, context=""):
        store[cache.regex_cache_key(prompt, model, context)] = payload

    monkeypatch.setattr(cache, "get_cached_regex", fake_get)
    monkeypatch.setattr(cache, "set_cached_regex", fake_set)
    return store


def test_samples_are_threaded_to_the_llm(settings, monkeypatch, in_memory_cache):
    settings.ANTHROPIC_API_KEY = "test-key"  # force the LLM branch
    captured = {}

    def fake_llm(prompt, columns, samples=None, action="auto"):
        captured["columns"] = columns
        captured["samples"] = samples
        value = samples["Railway"][0]
        return (
            [{"column": "Railway", "pattern": value, "explanation": "matches"}],
            "all",
            "matches the value",
            "replace",
            "",
        )

    monkeypatch.setattr(llm, "_generate_conditions_with_llm", fake_llm)

    out = llm.generate_conditions(
        "remove False", ["Railway"], samples={"Railway": ["False", "True"]}
    )
    assert out["source"] == "llm"
    # cased from the sample, not "false"
    assert out["predicates"][0]["pattern"] == "False"
    assert captured["samples"] == {"Railway": ["False", "True"]}


def test_llm_decomposes_a_multi_column_and(settings, monkeypatch, in_memory_cache):
    """The headline feature: one prompt -> per-column predicates + AND."""
    settings.ANTHROPIC_API_KEY = "test-key"

    def fake_llm(prompt, columns, samples=None, action="auto"):
        return (
            [
                {"column": "name", "pattern": "^A", "explanation": "name starts A"},
                {"column": "phone", "pattern": "^0", "explanation": "phone starts 0"},
            ],
            "all",
            "name starts with A and phone starts with 0",
            "replace",
            "",
        )

    monkeypatch.setattr(llm, "_generate_conditions_with_llm", fake_llm)

    out = llm.generate_conditions(
        "name starts with A and phone starts with 0", ["name", "phone"]
    )
    assert out["source"] == "llm"
    assert out["combinator"] == "all"
    assert [(p["column"], p["pattern"]) for p in out["predicates"]] == [
        ("name", "^A"),
        ("phone", "^0"),
    ]


def test_llm_predicate_for_unknown_column_is_rejected(
    settings, monkeypatch, in_memory_cache
):
    """A predicate must reference one of the selected columns, not a hallucinated
    one — otherwise Spark would fail deep in the job on a missing column."""
    settings.ANTHROPIC_API_KEY = "test-key"

    def fake_llm(prompt, columns, samples=None, action="auto"):
        return (
            [{"column": "ghost", "pattern": "^A", "explanation": "x"}],
            "all", "x", "replace", "",
        )

    monkeypatch.setattr(llm, "_generate_conditions_with_llm", fake_llm)

    with pytest.raises(RegexGenerationError):
        llm.generate_conditions("whatever", ["name", "phone"])


def test_same_prompt_different_data_is_not_cache_poisoned(
    settings, monkeypatch, in_memory_cache
):
    """The bug this fixes: 'remove False' over False/True data must not have its
    result served to a later 'remove False' over FALSE data — and an *identical*
    repeat must still be a cache hit (no redundant LLM call)."""
    settings.ANTHROPIC_API_KEY = "test-key"
    calls = []

    def fake_llm(prompt, columns, samples=None, action="auto"):
        calls.append(samples)
        value = samples["Railway"][0]
        return (
            [{"column": "Railway", "pattern": value, "explanation": f"matches {value}"}],
            "all",
            f"matches {value}",
            "replace",
            "",
        )

    monkeypatch.setattr(llm, "_generate_conditions_with_llm", fake_llm)

    a = llm.generate_conditions(
        "remove False", ["Railway"], samples={"Railway": ["False"]}
    )
    b = llm.generate_conditions(
        "remove False", ["Railway"], samples={"Railway": ["FALSE"]}
    )
    assert a["predicates"][0]["pattern"] == "False"
    assert b["predicates"][0]["pattern"] == "FALSE"  # right regex for its data
    assert len(calls) == 2                            # neither reused the other

    # The exact first request again -> cache hit, LLM not called a third time.
    again = llm.generate_conditions(
        "remove False", ["Railway"], samples={"Railway": ["False"]}
    )
    assert again["source"] == "cache"
    assert again["predicates"][0]["pattern"] == "False"
    assert len(calls) == 2


def test_heuristic_path_ignores_samples(settings, monkeypatch):
    """No API key -> deterministic heuristic; samples must not change behaviour
    and the cache stays prompt-only (context always empty)."""
    settings.ANTHROPIC_API_KEY = ""
    seen_context = []

    def fake_get(prompt, model, context=""):
        seen_context.append(context)
        return None

    monkeypatch.setattr(cache, "get_cached_regex", fake_get)
    monkeypatch.setattr(cache, "set_cached_regex", lambda *a, **k: None)

    out = llm.generate_conditions(
        "find email addresses", ["Email"], samples={"Email": ["a@b.com"]}
    )
    assert out["source"] == "heuristic"
    assert "@" in out["predicates"][0]["pattern"]
    assert seen_context == [""]  # data context never entered the heuristic key


# --------------------------------------------------------------------------- #
# Per-action system-prompt specialisation
# --------------------------------------------------------------------------- #
def test_auto_gets_the_action_selection_menu():
    sys = llm._system_for_action("auto")
    assert sys == llm._AUTO_SYSTEM_PROMPT
    assert "decide the `action`" in sys      # the verb->action menu is present
    assert "regex group 0" not in sys        # but no single-action shaping


def test_explicit_action_specialises_and_drops_the_menu():
    sys = llm._system_for_action("extract")
    assert llm._CORE_PROMPT in sys           # shared regex rules stay
    assert "regex group 0" in sys            # extract-specific shaping present
    assert "decide the `action`" not in sys  # the action menu is gone
    # every concrete action gets its own prompt, none of them the auto menu
    for a in ("find", "replace", "mask", "extract", "keep", "drop"):
        assert llm._system_for_action(a) != llm._AUTO_SYSTEM_PROMPT


def test_explicit_action_skips_inference_and_is_honoured(
    settings, monkeypatch, in_memory_cache
):
    """An explicit action is threaded to the LLM layer and comes back as the
    resolved action — the model is not asked to choose it."""
    settings.ANTHROPIC_API_KEY = "test-key"
    captured = {}

    def fake_llm(prompt, columns, samples=None, action="auto"):
        captured["action"] = action
        # explicit path: the layer echoes the requested action, empty value
        return (
            [{"column": "Card", "pattern": r"\d", "explanation": "digit"}],
            "all", "digits", action, "",
        )

    monkeypatch.setattr(llm, "_generate_conditions_with_llm", fake_llm)

    out = llm.generate_conditions("hide the card digits", ["Card"], action="mask")
    assert captured["action"] == "mask"   # request threaded through
    assert out["action"] == "mask"        # honoured, not re-inferred


def test_cache_is_namespaced_by_action(settings, monkeypatch, in_memory_cache):
    """Same words + data under different actions must not share a cache entry:
    the specialised prompt produces different predicates per action."""
    settings.ANTHROPIC_API_KEY = "test-key"
    calls = []

    def fake_llm(prompt, columns, samples=None, action="auto"):
        calls.append(action)
        resolved = action if action in llm._ACTIONS else "replace"
        return (
            [{"column": "c", "pattern": "x", "explanation": "x"}],
            "all", "x", resolved, "",
        )

    monkeypatch.setattr(llm, "_generate_conditions_with_llm", fake_llm)

    llm.generate_conditions("do it", ["c"], action="mask")
    llm.generate_conditions("do it", ["c"], action="extract")
    assert calls == ["mask", "extract"]   # neither action reused the other

    again = llm.generate_conditions("do it", ["c"], action="mask")
    assert again["source"] == "cache"     # same action -> cache hit
    assert calls == ["mask", "extract"]   # no third LLM call
