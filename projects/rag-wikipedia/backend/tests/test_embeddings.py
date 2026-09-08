"""The Embedder contract: every implementation must be able to state its dimension.

The vector store creates a collection at one fixed vector length, and vectors of
different lengths are not interchangeable. The length therefore has to come from
the model that will actually produce the vectors - bge-small 384, Vertex
text-embedding-005 768, text-embedding-3-large 3072 - and never from a constant
written next to the store, which is what pinned this project to a single
embedding model until `dim` became part of the interface.

These use a fake implementation on purpose: constructing BGEEmbedder loads
weights and revalidates them against the Hugging Face Hub, which is tens of
seconds and a network dependency the unit suite does not have.
"""

from __future__ import annotations

import pytest
from app.core.embeddings import BGEEmbedder, Embedder


class _FakeEmbedder(Embedder):
    """A conforming implementation, at a dimension that is not bge-small's."""

    @property
    def dim(self) -> int:
        return 768

    def embed(self, text: str) -> list[float]:
        return [0.1] * self.dim

    def embed_batch(self, texts: list[str], *, batch_size: int | None = None):
        return [self.embed(text) for text in texts]


def test_a_conforming_embedder_reports_its_dimension():
    assert _FakeEmbedder().dim == 768


def test_the_reported_dimension_matches_the_vectors_produced():
    # The contract is only worth anything if the number describes the vectors.
    embedder = _FakeEmbedder()
    assert len(embedder.embed("text")) == embedder.dim


def test_an_implementation_without_dim_cannot_be_constructed():
    # The point of making it abstract: a new adapter that forgets to answer
    # fails loudly at construction, not silently at the first upsert.
    class MissingDim(Embedder):
        def embed(self, text: str) -> list[float]:
            return [0.1]

        def embed_batch(self, texts: list[str], *, batch_size: int | None = None):
            return [[0.1] for _ in texts]

    with pytest.raises(TypeError, match="dim"):
        MissingDim()


def test_bge_declares_dim_without_loading_the_model():
    # Checked on the CLASS: instantiating it would download and load weights.
    assert isinstance(BGEEmbedder.__dict__["dim"], property)
