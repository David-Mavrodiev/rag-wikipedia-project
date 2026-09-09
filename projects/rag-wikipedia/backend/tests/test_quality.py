import json
from unittest.mock import MagicMock

from app.core import quality as quality_state
from app.core.quality import update_quality_state
from app.core.runtime_config import get_runtime_config


def test_quality_endpoint_returns_runtime_state(client):
    update_quality_state(
        status="healthy",
        metrics={"golden": {"recall@5": 1.0}},
        active_config={"retrieval_candidate_k": get_runtime_config().retrieval_candidate_k},
        reason="test",
    )

    response = client.get("/quality")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_quality_audit_endpoint_updates_state(client, monkeypatch):
    reports = {
        "golden": {
            "recall@5": 1.0,
            "precision@5": 1.0,
            "mrr": 1.0,
            "refusal_accuracy": 1.0,
            "false_accept_rate": 0.0,
            "answerable_refusal_rate": 0.0,
        },
        "holdout": {
            "recall@5": 1.0,
            "precision@5": 1.0,
            "mrr": 1.0,
            "refusal_accuracy": 1.0,
            "false_accept_rate": 0.0,
            "answerable_refusal_rate": 0.0,
        },
        "adversarial": {
            "recall@5": 1.0,
            "precision@5": 1.0,
            "mrr": 1.0,
            "refusal_accuracy": 1.0,
            "false_accept_rate": 0.0,
            "answerable_refusal_rate": 0.0,
        },
    }

    monkeypatch.setattr("app.core.embeddings.BGEEmbedder", lambda model_name: object())
    monkeypatch.setattr("app.core.vectorstore.QdrantStore", lambda url, collection: object())
    monkeypatch.setattr(
        "eval.audit.evaluate_datasets",
        lambda base_dir, embedder, store, k: reports,
    )
    monkeypatch.setattr("eval.audit.audit_failures", lambda reports, k: [])
    monkeypatch.setattr("eval.audit.summarize_reports", lambda reports, k: reports)

    response = client.post("/quality/audit")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert client.get("/quality").json()["status"] == "healthy"


def test_quality_endpoint_loads_latest_audit_report(client, monkeypatch, tmp_path):
    report_path = tmp_path / "audit_report.json"
    report_path.write_text(
        """
        {
          "status": "healthy",
          "failures": [],
          "datasets": {"golden": {"recall@5": 1.0}},
          "active_config": {"retrieval_candidate_k": 20}
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(quality_state, "AUDIT_REPORT_PATH", report_path)
    update_quality_state(status="unknown", metrics={}, active_config={}, reason="reset")

    response = client.get("/quality")

    assert response.status_code == 200
    assert response.json()["metrics"]["golden"]["recall@5"] == 1.0


# --- hardening: the audit must not run on the event loop ---------------------

def _mock_audit(monkeypatch, *, failures=None, corrected=False):
    """Stub out every expensive part of the audit, leaving the wiring intact."""
    metrics = {
        name: {
            "recall@5": 1.0,
            "precision@5": 1.0,
            "mrr": 1.0,
            "refusal_accuracy": 1.0,
            "false_accept_rate": 0.0,
            "answerable_refusal_rate": 0.0,
        }
        for name in ("golden", "holdout", "adversarial")
    }
    monkeypatch.setattr("app.core.embeddings.BGEEmbedder", lambda model_name: object())
    monkeypatch.setattr("app.core.vectorstore.QdrantStore", lambda url, collection: object())
    monkeypatch.setattr(
        "eval.audit.evaluate_datasets", lambda base_dir, embedder, store, k: metrics
    )
    monkeypatch.setattr("eval.audit.audit_failures", lambda reports, k: list(failures or []))
    monkeypatch.setattr("eval.audit.summarize_reports", lambda reports, k: reports)
    monkeypatch.setattr(
        "eval.audit.try_auto_correct",
        lambda base_dir, embedder, store, k: (corrected, metrics if corrected else {}),
    )
    return metrics


def test_audit_is_dispatched_to_a_worker_thread(client, monkeypatch):
    # The audit loads the embedder, hits Qdrant and scores every case. Awaiting
    # it inline froze the whole API for the duration.
    from app.api import quality as quality_api

    _mock_audit(monkeypatch)
    calls = []
    real = quality_api.run_in_threadpool

    async def spy(func, *args, **kwargs):
        calls.append(func)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(quality_api, "run_in_threadpool", spy)

    assert client.post("/quality/audit").status_code == 200
    assert calls, "audit must be handed to run_in_threadpool, not awaited inline"


def test_concurrent_audit_is_rejected_with_409(client, monkeypatch):
    from app.api import quality as quality_api

    _mock_audit(monkeypatch)

    class BusyLock:
        def locked(self):
            return True

    monkeypatch.setattr(quality_api, "_audit_lock", BusyLock())

    response = client.post("/quality/audit")

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


# --- hardening: only the mutating path is privileged --------------------------

def test_plain_audit_needs_no_token(client, monkeypatch):
    # Read-only scoring stays open so the dashboard button keeps working.
    _mock_audit(monkeypatch)

    assert client.post("/quality/audit").status_code == 200


def test_auto_correct_is_disabled_without_a_configured_token(client, monkeypatch):
    from app.core.config import settings

    _mock_audit(monkeypatch, failures=["golden: recall@5=0.10 < 0.8"])
    monkeypatch.setattr(settings, "quality_admin_token", "")

    response = client.post("/quality/audit?auto_correct=true")

    assert response.status_code == 503


def test_auto_correct_rejects_a_wrong_token(client, monkeypatch):
    from app.core.config import settings

    _mock_audit(monkeypatch, failures=["golden: recall@5=0.10 < 0.8"])
    monkeypatch.setattr(settings, "quality_admin_token", "expected-token")

    response = client.post(
        "/quality/audit?auto_correct=true", headers={"X-Quality-Token": "wrong-token"}
    )

    assert response.status_code == 403


def test_auto_correct_accepts_the_configured_token(client, monkeypatch):
    from app.core.config import settings

    _mock_audit(monkeypatch, failures=["golden: recall@5=0.10 < 0.8"], corrected=True)
    monkeypatch.setattr(settings, "quality_admin_token", "expected-token")

    response = client.post(
        "/quality/audit?auto_correct=true", headers={"X-Quality-Token": "expected-token"}
    )

    assert response.status_code == 200
    assert response.json()["corrected"] is True


# --- hardening: one response shape --------------------------------------------

def test_audit_response_matches_the_quality_state_shape(client, monkeypatch):
    # POST used to return `datasets` where GET returned `metrics`, so the panel
    # rendered an empty table right after a successful audit.
    _mock_audit(monkeypatch)

    posted = client.post("/quality/audit").json()
    fetched = client.get("/quality").json()

    assert set(posted) == set(fetched) | {"failures", "corrected"}
    assert posted["metrics"] == fetched["metrics"]
    assert posted["status"] == fetched["status"]


# --- hardening: an unreadable report must not 500 -----------------------------

def test_quality_endpoint_survives_a_truncated_audit_report(client, monkeypatch, tmp_path):
    report_path = tmp_path / "audit_report.json"
    report_path.write_text('{"status": "healthy", "datas', encoding="utf-8")  # half-written
    monkeypatch.setattr(quality_state, "AUDIT_REPORT_PATH", report_path)
    update_quality_state(status="unknown", metrics={}, active_config={}, reason="reset")

    response = client.get("/quality")

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


def test_quality_endpoint_survives_a_non_object_audit_report(client, monkeypatch, tmp_path):
    report_path = tmp_path / "audit_report.json"
    report_path.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(quality_state, "AUDIT_REPORT_PATH", report_path)
    update_quality_state(status="unknown", metrics={}, active_config={}, reason="reset")

    response = client.get("/quality")

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


def test_invalid_golden_set_returns_422_not_a_torn_down_request(client, monkeypatch):
    # validate_golden_set used to raise SystemExit - a BaseException that slips
    # past Starlette's exception middleware, so the request died without a
    # response instead of reporting bad input.
    from eval.run_eval import InvalidGoldenSet

    _mock_audit(monkeypatch)

    def explode(base_dir, embedder, store, k):
        raise InvalidGoldenSet("golden.jsonl must contain >= 20 cases")

    monkeypatch.setattr("eval.audit.evaluate_datasets", explode)

    response = client.post("/quality/audit")

    assert response.status_code == 422
    assert "20 cases" in response.json()["detail"]


# --- provenance: metrics without their conditions are unfalsifiable ---------

def test_provenance_records_what_was_measured(monkeypatch):
    from app.core.quality import build_provenance

    store = MagicMock()
    store.count.return_value = 6358

    prov = build_provenance(store=store, top_k=5)

    # The same code and suites score differently on a 60-article corpus and a
    # 500-article one, so a report that does not say which is unreadable.
    assert prov["vector_count"] == 6358
    assert prov["top_k"] == 5
    assert prov["profile"] and prov["collection"] and prov["embed_model"]
    assert prov["generated_at"]
    assert set(prov["gates"]) == {
        "recall",
        "precision",
        "refusal_accuracy",
        "false_accept_rate_max",
    }


def test_provenance_survives_an_unreadable_collection():
    # A missing collection must not take the audit down with it.
    from app.core.quality import build_provenance

    store = MagicMock()
    store.count.side_effect = RuntimeError("collection not found")

    prov = build_provenance(store=store, top_k=5)

    assert prov["vector_count"] is None
    assert prov["profile"]


def test_audit_response_carries_provenance(client, monkeypatch):
    _mock_audit(monkeypatch)

    posted = client.post("/quality/audit").json()
    fetched = client.get("/quality").json()

    assert "provenance" in posted and "provenance" in fetched
    assert posted["provenance"]["top_k"] == 5
    # POST and GET must keep the same shape - they diverged once before.
    assert set(posted) == set(fetched) | {"failures", "corrected"}


# --- a deployment must not present a foreign measurement as its own ---------

def _report_measuring(collection: str, profile: str = "tiny") -> str:
    return json.dumps(
        {
            "status": "healthy",
            "failures": [],
            "datasets": {"golden": {"recall@5": 1.0}},
            "active_config": {"retrieval_candidate_k": 20},
            "provenance": {"collection": collection, "profile": profile, "vector_count": 123},
        }
    )


def test_committed_report_is_labelled_as_measured_elsewhere(client, monkeypatch, tmp_path):
    from app.core.config import settings

    path = tmp_path / "audit_report.json"
    path.write_text(_report_measuring(settings.collection), encoding="utf-8")
    monkeypatch.setattr(quality_state, "AUDIT_REPORT_PATH", path)

    body = client.get("/quality").json()

    # It matches this deployment, so it is served - but never as if this
    # deployment had measured it.
    assert body["status"] == "healthy"
    assert body["source"] == "committed_report"


def test_report_for_a_different_corpus_is_refused(client, monkeypatch, tmp_path):
    # The committed report travels in the image. If the deployment serves a
    # different collection, those numbers describe someone else's data and
    # reporting them as this deployment's quality would be a lie.
    path = tmp_path / "audit_report.json"
    path.write_text(_report_measuring("some_other_collection"), encoding="utf-8")
    monkeypatch.setattr(quality_state, "AUDIT_REPORT_PATH", path)

    body = client.get("/quality").json()

    assert body["status"] == "unknown"
    assert body["source"] == "none"
    assert "some_other_collection" in body["reason"]


def test_an_in_process_audit_is_labelled_as_such(client, monkeypatch):
    _mock_audit(monkeypatch)

    assert client.post("/quality/audit").json()["source"] == "in_process"
