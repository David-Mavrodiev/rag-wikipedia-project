"""Runtime retrieval config: bounds, atomic persistence, absolute location.

These five values steer refusal behaviour and are writable at runtime by the
auto-correcting audit, so an unvalidated or half-written file changes what the
API refuses.
"""

import json
from dataclasses import replace

import pytest
from app.core import runtime_config as rc


def _valid() -> rc.RetrievalRuntimeConfig:
    return rc.RetrievalRuntimeConfig(
        refusal_min_score=0.45,
        refusal_high_confidence_score=0.78,
        refusal_min_margin=0.02,
        refusal_min_overlap_terms=1,
        retrieval_candidate_k=20,
    )


def test_config_path_is_absolute():
    # A relative path resolved against the process working directory, so the
    # file landed somewhere different depending on where uvicorn was started.
    assert rc.CONFIG_PATH.is_absolute()
    assert rc.CONFIG_PATH.name == "runtime_config.json"


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "runtime_config.json"
    monkeypatch.setattr(rc, "_active_config", _valid())

    rc.save_runtime_config(path)

    assert rc.load_runtime_config(path) == _valid()


def test_save_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    path = tmp_path / "runtime_config.json"
    monkeypatch.setattr(rc, "_active_config", _valid())

    rc.save_runtime_config(path)

    assert [entry.name for entry in tmp_path.iterdir()] == ["runtime_config.json"]


def test_save_replaces_previous_contents_whole(tmp_path, monkeypatch):
    path = tmp_path / "runtime_config.json"
    path.write_text("x" * 5000, encoding="utf-8")
    monkeypatch.setattr(rc, "_active_config", _valid())

    rc.save_runtime_config(path)

    assert json.loads(path.read_text(encoding="utf-8"))["retrieval_candidate_k"] == 20


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("refusal_min_score", 1.5),
        ("refusal_min_score", -0.1),
        ("refusal_high_confidence_score", 2.0),
        ("refusal_min_margin", -1.0),
        ("refusal_min_overlap_terms", -1),
        ("retrieval_candidate_k", 0),
        ("retrieval_candidate_k", -20),
    ],
)
def test_out_of_range_values_are_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        rc.validate_runtime_config(replace(_valid(), **{field: value}))


def test_apply_rejects_an_invalid_config(monkeypatch):
    monkeypatch.setattr(rc, "_active_config", _valid())

    with pytest.raises(ValueError):
        rc.apply_runtime_config(replace(_valid(), retrieval_candidate_k=0))

    assert rc.get_runtime_config() == _valid()


def test_load_rejects_out_of_range_file(tmp_path):
    path = tmp_path / "runtime_config.json"
    payload = {
        "refusal_min_score": 42.0,
        "refusal_high_confidence_score": 0.78,
        "refusal_min_margin": 0.02,
        "refusal_min_overlap_terms": 1,
        "retrieval_candidate_k": 20,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="refusal_min_score"):
        rc.load_runtime_config(path)


def test_load_reports_missing_keys(tmp_path):
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps({"refusal_min_score": 0.45}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing"):
        rc.load_runtime_config(path)


def test_load_ignores_unknown_keys(tmp_path, monkeypatch):
    path = tmp_path / "runtime_config.json"
    monkeypatch.setattr(rc, "_active_config", _valid())
    rc.save_runtime_config(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["a_key_from_a_future_version"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert rc.load_runtime_config(path) == _valid()


def test_load_rejects_a_non_object_file(tmp_path):
    path = tmp_path / "runtime_config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        rc.load_runtime_config(path)


def test_a_config_written_before_the_coverage_field_still_loads(tmp_path):
    """A runtime_config.json persisted by an older build must not break startup.

    The file is written by /quality/audit?auto_correct=true, so a live system
    can be holding one from before `refusal_min_evidence_coverage` existed.
    Making the field required would have turned a routine deploy into a startup
    failure over a missing key whose absence has an obvious meaning: the
    coverage gate had not been configured, so it is off.
    """
    path = tmp_path / "runtime_config.json"
    path.write_text(
        json.dumps(
            {
                "refusal_min_score": 0.45,
                "refusal_high_confidence_score": 0.78,
                "refusal_min_margin": 0.02,
                "refusal_min_overlap_terms": 1,
                "retrieval_candidate_k": 20,
            }
        ),
        encoding="utf-8",
    )

    loaded = rc.load_runtime_config(path)

    assert loaded == _valid()
    # Off, not some fitted value: an absent setting must never silently enable
    # a gate the operator never configured.
    assert loaded.refusal_min_evidence_coverage == 0.0


def test_a_field_with_no_default_is_still_required(tmp_path):
    # The relaxation above is scoped to DEFAULTED fields only. A genuinely
    # missing required key must still raise rather than be filled in.
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps({"refusal_min_score": 0.45}), encoding="utf-8")

    with pytest.raises(ValueError, match="is missing"):
        rc.load_runtime_config(path)
