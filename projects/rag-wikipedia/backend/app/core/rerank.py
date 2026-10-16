"""Cross-encoder relevance: a second opinion on whether a passage is ON TOPIC.

The IDF coverage gate asks whether the question's rare words appear in the
retrieved text. Its measured weakness is that a NEIGHBOURING article supplies
them: 16 of the 20 held-out unanswerable questions clear it (false_accept
0.800 at retrieval), and every fabrication in the first end-to-end run came from
one of those. A bag of rare terms cannot tell "this text is about the thing you
asked" from "this text shares words with your question". A cross-encoder reads
the question and the passage together, which is exactly that distinction.

OFF unless RERANK_MODEL is set, for two reasons. A model loaded on a default
makes every deployment pay for it, and a gate whose threshold has not been
fitted and published is not a gate, it is a guess.

Failure is deliberately OPEN. If the model cannot be loaded, scoring returns
None, the caller keeps the IDF gate, and the event is logged at ERROR - this is
a quality filter, not an access control, so a broken download must not take a
running service down. `providers/azure_search.py` fails CLOSED for the opposite
reason: there, passing through is a data leak.
"""

from __future__ import annotations

import logging
import math

from app.core.config import settings

logger = logging.getLogger(__name__)

# One per process, like the embedder. Loading a cross-encoder per request would
# dominate the latency it is meant to inform.
_model = None
_load_failed = False


def _sigmoid(x: float) -> float:
    """ms-marco cross-encoders emit an unbounded logit; a threshold wants 0-1."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp = math.exp(x)  # avoids overflow for very negative logits
    return exp / (1.0 + exp)


def enabled() -> bool:
    return bool(settings.rerank_model)


def reset_rerank_state() -> None:
    """For tests, which otherwise inherit each other's loaded model."""
    global _model, _load_failed
    _model, _load_failed = None, False


def _load():
    """The cross-encoder, or None. Loads once; a failure is not retried."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    try:
        from sentence_transformers import CrossEncoder  # noqa: PLC0415

        try:
            # Cache first, exactly as embeddings.py does: an unauthenticated Hub
            # revalidation cost that path 60 s on a cached model.
            _model = CrossEncoder(settings.rerank_model, local_files_only=True)
        except Exception:
            _model = CrossEncoder(settings.rerank_model)
    except Exception as exc:
        _load_failed = True
        logger.error(
            "Reranker %s could not be loaded (%s); falling back to the IDF gate. "
            "Relevance scores will be absent from this process.",
            settings.rerank_model, exc,
        )
        return None
    return _model


def relevance_scores(query: str, results: list[dict]) -> list[float] | None:
    """Relevance of each result to *query*, 0-1, or None when unavailable.

    None means "not measured" and is distinct from a list of zeros, which would
    mean "measured, nothing is relevant" - and would refuse every question.
    """
    if not enabled() or not results:
        return None
    model = _load()
    if model is None:
        return None
    pairs = [(query, result.get("text", "")) for result in results]
    try:
        raw = model.predict(pairs)
    except Exception as exc:  # a scoring failure must not fail the request
        logger.error("Reranker scoring failed (%s); falling back to the IDF gate.", exc)
        return None
    return [_sigmoid(float(value)) for value in raw]
