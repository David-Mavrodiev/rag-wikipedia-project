import pytest
from pydantic import ValidationError


def test_defaults():
    from app.core.config import Settings

    settings = Settings()
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.top_k == 5
    assert settings.profile == "tiny"
    assert settings.refusal_min_score == 0.45
    assert settings.retrieval_candidate_k == 20


def test_env_override(monkeypatch):
    monkeypatch.setenv("TOP_K", "10")
    monkeypatch.setenv("PROFILE", "real")

    from app.core.config import Settings

    settings = Settings()
    assert settings.top_k == 10
    assert settings.profile == "real"


def test_the_coverage_gate_ships_at_the_measured_threshold():
    # Not a plausible-looking tuning knob: 0.45 was measured on the SERVING
    # corpus with scripts/sweep_coverage.py - false_accept_rate 0.600 -> 0.500
    # for answerable_refusal_rate 0.000 -> 0.033 - and 0.70, which scores a
    # better false_accept, was rejected for refusing 18.3% of answerable
    # questions. Pinned so that moving it has to be a deliberate edit to a test
    # that says what the number cost.
    from app.core.config import Settings

    assert Settings().refusal_min_evidence_coverage == 0.45


def test_quality_admin_token_defaults_to_disabled():
    from app.core.config import Settings

    assert Settings().quality_admin_token == ""


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TOP_K", "0"),
        ("TOP_K", "-5"),
        ("REFUSAL_MIN_SCORE", "1.5"),
        ("REFUSAL_MIN_SCORE", "-0.2"),
        ("REFUSAL_HIGH_CONFIDENCE_SCORE", "3"),
        ("REFUSAL_MIN_OVERLAP_TERMS", "-1"),
        ("RETRIEVAL_CANDIDATE_K", "0"),
        ("RATE_LIMIT_QUERY_PER_MINUTE", "0"),
    ],
)
def test_out_of_range_environment_values_are_rejected(monkeypatch, variable, value):
    # Without bounds these were accepted silently: a negative candidate_k or a
    # refusal score above 1.0 quietly changed what the API refuses.
    monkeypatch.setenv(variable, value)

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    ("variable", "value", "attribute", "expected"),
    [
        ("TOP_K", "10", "top_k", 10),
        ("REFUSAL_MIN_SCORE", "0.9", "refusal_min_score", 0.9),
        ("RETRIEVAL_CANDIDATE_K", "50", "retrieval_candidate_k", 50),
    ],
)
def test_in_range_environment_values_are_accepted(
    monkeypatch, variable, value, attribute, expected
):
    monkeypatch.setenv(variable, value)

    from app.core.config import Settings

    assert getattr(Settings(), attribute) == expected
