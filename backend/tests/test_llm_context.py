"""Data-sample context: the LLM sees real column values, and the regex cache is
scoped to that data so one dataset's result can't be served to another."""
import pytest

from processing import cache, llm


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
    assert with_ctx.startswith("regex:v2:")


# --------------------------------------------------------------------------- #
# End-to-end wiring through generate_regex (LLM path, no network)
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

    def fake_llm(prompt, columns, samples=None):
        captured["columns"] = columns
        captured["samples"] = samples
        return samples["Railway"][0], "matches the value"

    monkeypatch.setattr(llm, "_generate_with_llm", fake_llm)

    out = llm.generate_regex(
        "remove False", ["Railway"], samples={"Railway": ["False", "True"]}
    )
    assert out["source"] == "llm"
    assert out["pattern"] == "False"  # cased from the sample, not "false"
    assert captured["samples"] == {"Railway": ["False", "True"]}


def test_same_prompt_different_data_is_not_cache_poisoned(
    settings, monkeypatch, in_memory_cache
):
    """The bug this fixes: 'remove False' over False/True data must not have its
    result served to a later 'remove False' over FALSE data — and an *identical*
    repeat must still be a cache hit (no redundant LLM call)."""
    settings.ANTHROPIC_API_KEY = "test-key"
    calls = []

    def fake_llm(prompt, columns, samples=None):
        calls.append(samples)
        value = samples["Railway"][0]
        return value, f"matches {value}"

    monkeypatch.setattr(llm, "_generate_with_llm", fake_llm)

    a = llm.generate_regex("remove False", ["Railway"], samples={"Railway": ["False"]})
    b = llm.generate_regex("remove False", ["Railway"], samples={"Railway": ["FALSE"]})
    assert a["pattern"] == "False"
    assert b["pattern"] == "FALSE"          # served the right regex for its data
    assert len(calls) == 2                  # neither wrongly reused the other

    # The exact first request again -> cache hit, LLM not called a third time.
    again = llm.generate_regex(
        "remove False", ["Railway"], samples={"Railway": ["False"]}
    )
    assert again["source"] == "cache"
    assert again["pattern"] == "False"
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

    out = llm.generate_regex(
        "find email addresses", ["Email"], samples={"Email": ["a@b.com"]}
    )
    assert out["source"] == "heuristic"
    assert "@" in out["pattern"]
    assert seen_context == [""]  # data context never entered the heuristic key
