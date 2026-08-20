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
