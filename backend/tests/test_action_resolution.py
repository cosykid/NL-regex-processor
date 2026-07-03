"""How a job's requested action + the model's inferred action combine.

``_resolve_action`` decides the concrete action (and value) that actually runs:
an explicit request wins; ``auto`` defers to what the model inferred, but a value
the user typed still beats a value the model pulled from the prompt.
"""
from jobs.models import Job
from processing.tasks import _resolve_action


def _job(action, replacement_value=""):
    # Unsaved instance — _resolve_action only reads attributes, no DB needed.
    return Job(action=action, replacement_value=replacement_value)


def test_explicit_action_is_honoured_over_inferred():
    job = _job(Job.Action.MASK, replacement_value="•")
    action, value = _resolve_action(job, {"action": "keep", "value": "ignored"})
    assert action == "mask"
    assert value == "•"


def test_auto_uses_inferred_action():
    job = _job(Job.Action.AUTO)
    action, value = _resolve_action(job, {"action": "extract", "value": ""})
    assert action == "extract"


def test_auto_defaults_to_replace_when_model_names_nothing():
    job = _job(Job.Action.AUTO)
    action, _ = _resolve_action(job, {})
    assert action == "replace"


def test_auto_typed_value_beats_inferred_value():
    job = _job(Job.Action.AUTO, replacement_value="TYPED")
    _, value = _resolve_action(job, {"action": "replace", "value": "inferred"})
    assert value == "TYPED"


def test_auto_falls_back_to_inferred_value_when_box_empty():
    job = _job(Job.Action.AUTO, replacement_value="")
    _, value = _resolve_action(job, {"action": "replace", "value": "REDACTED"})
    assert value == "REDACTED"


def test_auto_rejects_bogus_inferred_action():
    job = _job(Job.Action.AUTO)
    action, _ = _resolve_action(job, {"action": "nonsense"})
    assert action == "replace"
