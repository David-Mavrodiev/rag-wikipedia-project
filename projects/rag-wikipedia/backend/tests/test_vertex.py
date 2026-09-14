"""The Vertex adapters must honour the same contracts as the shipped models.

NO GCP ACCOUNT, NO BILLING, NO `gcp` EXTRA. Every test here patches
`sys.modules` with a fake SDK, exactly as `tests/test_llm.py` does for ollama.
That is what makes the cloud-agnostic claim checkable in CI rather than
asserted in a README: the contracts are enforced against a second provider on
every run, on a machine that has never authenticated to Google.

What this CANNOT prove is the shape of a real Vertex response. It proves that
whatever this adapter is handed, it puts a deterministic decoding config on the
wire, probes its dimension instead of declaring one, honours the batch keyword
the ingest pipeline actually passes, and refuses to align a response it cannot
align. Those are the ways an adapter silently corrupts a corpus.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.core.embeddings import Embedder
from app.core.llm import LLM
from providers.vertex import MAX_EMBED_INSTANCES, VertexEmbedder, VertexLLM

DIM = 768


def _fake_sdk(dim: int = DIM, answer: str | None = "an answer") -> dict[str, MagicMock]:
    """A stand-in for the Vertex SDK, shaped like the parts these adapters use.

    Embeddings encode the LENGTH of their input text in the first component, so
    a test can tell which vector came from which string and assert that order
    survived batching.
    """
    vertexai = MagicMock()
    generative = MagicMock()
    language = MagicMock()

    generative.GenerativeModel.return_value.generate_content.return_value.text = answer

    def _get_embeddings(texts):
        return [SimpleNamespace(values=[float(len(t))] + [0.0] * (dim - 1)) for t in texts]

    language.TextEmbeddingModel.from_pretrained.return_value.get_embeddings.side_effect = (
        _get_embeddings
    )

    return {
        "vertexai": vertexai,
        "vertexai.generative_models": generative,
        "vertexai.language_models": language,
    }


def _llm(modules) -> VertexLLM:
    with patch.dict("sys.modules", modules):
        return VertexLLM(project="a-project")


def _embedder(modules) -> VertexEmbedder:
    with patch.dict("sys.modules", modules):
        return VertexEmbedder(project="a-project")


def _generate_content(modules):
    return modules["vertexai.generative_models"].GenerativeModel.return_value.generate_content


def _embed_model(modules):
    return modules["vertexai.language_models"].TextEmbeddingModel.from_pretrained.return_value


# --------------------------------------------------------------------------
# The LLM contract: deterministic decoding, from EVERY adapter.
# --------------------------------------------------------------------------


def test_vertex_llm_satisfies_the_interface():
    assert isinstance(_llm(_fake_sdk()), LLM)


def test_generate_returns_the_models_text():
    assert _llm(_fake_sdk()).generate("prompt") == "an answer"


def test_generate_requests_temperature_zero():
    # Vertex defaults to 1.0. Without this, CHOOSING a provider would change
    # answer behaviour - the one thing an interchangeable interface exists to
    # prevent, and the same bug tests/test_llm.py pins for Ollama.
    modules = _fake_sdk()

    _llm(modules).generate("prompt")

    _, kwargs = _generate_content(modules).call_args
    assert kwargs["generation_config"]["temperature"] == 0.0


def test_generate_pins_the_sampling_seed():
    modules = _fake_sdk()

    _llm(modules).generate("prompt")

    _, kwargs = _generate_content(modules).call_args
    assert kwargs["generation_config"]["seed"] == 0


def test_a_blocked_generation_becomes_empty_text_not_none():
    # A safety-blocked candidate surfaces as a null `text`. The chain treats an
    # empty generation as a refusal; handing it None would raise instead.
    assert _llm(_fake_sdk(answer=None)).generate("prompt") == ""


# --------------------------------------------------------------------------
# The Embedder contract: PROBE the dimension, never declare it.
# --------------------------------------------------------------------------


def test_vertex_embedder_satisfies_the_interface():
    assert isinstance(_embedder(_fake_sdk()), Embedder)


def test_dim_is_probed_from_the_model():
    # 768 is never written down in the adapter - it comes back from the model.
    # A declared dimension is one that can be declared wrongly, and it builds a
    # collection that then rejects the vectors it is asked to hold.
    assert _embedder(_fake_sdk(dim=768)).dim == 768


def test_dim_reflects_whatever_the_model_actually_returns():
    # Guards the test above from passing on a hardcoded 768: a different model
    # must give a different answer without the adapter being touched.
    assert _embedder(_fake_sdk(dim=1536)).dim == 1536


def test_dim_is_probed_once_and_cached():
    # Callers read this per ingest, not per chunk, but it is still a billed
    # call - so it must not repeat.
    modules = _fake_sdk()
    embedder = _embedder(modules)

    assert embedder.dim == embedder.dim == DIM
    assert _embed_model(modules).get_embeddings.call_count == 1


def test_dim_agrees_with_the_vectors_actually_produced():
    embedder = _embedder(_fake_sdk())
    assert len(embedder.embed("hello")) == embedder.dim


# --------------------------------------------------------------------------
# Batching: the keyword the ingest pipeline really passes, and order.
# --------------------------------------------------------------------------


def test_embed_batch_honours_the_batch_size_keyword():
    # pipeline/tasks.py calls embed_batch(..., batch_size=64). An adapter that
    # drops the keyword raises TypeError on the first real ingest, not here.
    modules = _fake_sdk()

    _embedder(modules).embed_batch(["a", "bb", "ccc", "dddd"], batch_size=2)

    assert _embed_model(modules).get_embeddings.call_count == 2


def test_embed_batch_preserves_input_order_across_windows():
    # The first component encodes the input's length, so this asserts that
    # vector i really did come from text i - the corruption that would
    # otherwise be silent.
    vectors = _embedder(_fake_sdk()).embed_batch(["a", "bb", "ccc", "dddd"], batch_size=2)

    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0]


def test_a_response_that_cannot_be_aligned_is_rejected_loudly():
    # Vertex returns a positional list with no per-item index, so position is
    # the only alignment available and the count is the only check. Zipping a
    # short response onto its inputs would attach vectors to the WRONG chunks:
    # ingestion succeeds, retrieval returns confident nonsense, nothing fails.
    modules = _fake_sdk()
    embedder = _embedder(modules)
    _embed_model(modules).get_embeddings.side_effect = lambda texts: [SimpleNamespace(values=[1.0])]

    with pytest.raises(ValueError, match="cannot be aligned"):
        embedder.embed_batch(["a", "bb", "ccc"])


def test_embed_batch_of_nothing_calls_nothing():
    modules = _fake_sdk()

    assert _embedder(modules).embed_batch([]) == []
    assert _embed_model(modules).get_embeddings.call_count == 0


def test_a_window_larger_than_the_provider_cap_is_clamped():
    # Vertex rejects more than MAX_EMBED_INSTANCES per request. A caller asking
    # for more is not an error to raise, it is a window to split.
    modules = _fake_sdk()
    texts = ["x"] * (MAX_EMBED_INSTANCES + 50)

    _embedder(modules).embed_batch(texts, batch_size=len(texts))

    calls = _embed_model(modules).get_embeddings.call_args_list
    assert [len(call.args[0]) for call in calls] == [MAX_EMBED_INSTANCES, 50]
