"""Startup restore of a persisted retrieval config.

A successful `--auto-correct` audit saves new refusal thresholds. Before this
was wired in, nothing read that file back and the correction evaporated on the
next restart.
"""

import json
from dataclasses import asdict, replace

from app.core import runtime_config as rc
from app.main import restore_runtime_config


def _tuned() -> rc.RetrievalRuntimeConfig:
    return replace(rc.default_runtime_config(), refusal_min_score=0.6, retrieval_candidate_k=30)


def _write(path, config) -> None:
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def test_persisted_config_is_restored(tmp_path, monkeypatch):
    path = tmp_path / "runtime_config.json"
    _write(path, _tuned())
    monkeypatch.setattr(rc, "_active_config", rc.default_runtime_config())

    restore_runtime_config(path)

    assert rc.get_runtime_config() == _tuned()


def test_restore_logs_the_applied_values(tmp_path, monkeypatch, caplog):
    # These five values decide what the API refuses. A config that is merely
    # wrong rather than malformed is still valid, so the startup log is the only
    # place a human sees that the running thresholds are not the defaults.
    path = tmp_path / "runtime_config.json"
    _write(path, _tuned())
    monkeypatch.setattr(rc, "_active_config", rc.default_runtime_config())

    with caplog.at_level("INFO"):
        restore_runtime_config(path)

    assert "Restored retrieval config" in caplog.text
    assert "refusal_min_score=0.6" in caplog.text.replace(" ", "")


def test_missing_file_falls_back_to_defaults(tmp_path, monkeypatch, caplog):
    path = tmp_path / "absent.json"
    monkeypatch.setattr(rc, "_active_config", rc.default_runtime_config())

    with caplog.at_level("INFO"):
        restore_runtime_config(path)

    assert rc.get_runtime_config() == rc.default_runtime_config()
    assert "using defaults" in caplog.text


def test_out_of_range_file_is_ignored_not_applied(tmp_path, monkeypatch, caplog):
    # Startup must survive a corrupted or hand-edited file. Applying it would
    # let a bad persisted value silently change refusal behaviour on every boot.
    path = tmp_path / "runtime_config.json"
    payload = asdict(_tuned())
    payload["refusal_min_score"] = 42.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(rc, "_active_config", rc.default_runtime_config())

    with caplog.at_level("WARNING"):
        restore_runtime_config(path)

    assert rc.get_runtime_config() == rc.default_runtime_config()
    assert "Ignoring unusable" in caplog.text


def test_truncated_file_is_ignored_not_applied(tmp_path, monkeypatch, caplog):
    path = tmp_path / "runtime_config.json"
    path.write_text('{"refusal_min_score": 0.6, "retrieval', encoding="utf-8")
    monkeypatch.setattr(rc, "_active_config", rc.default_runtime_config())

    with caplog.at_level("WARNING"):
        restore_runtime_config(path)

    assert rc.get_runtime_config() == rc.default_runtime_config()
    assert "Ignoring unusable" in caplog.text
