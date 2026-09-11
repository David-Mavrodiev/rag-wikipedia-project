from __future__ import annotations

from abc import ABC, abstractmethod


class Embedder(ABC):
    @property
    @abstractmethod
    def dim(self) -> int:
        """The length of the vectors this embedder produces.

        CONTRACT - PROBE this, never declare it. Dimensions are not uniform
        (bge-small 384, Vertex text-embedding-005 768, text-embedding-3-small
        1536, -3-large 3072), and an Azure or Vertex deployment serves whatever
        model it was created with. A declared guess builds a collection that
        then rejects the vectors it is asked to hold, and the mismatch surfaces
        mid-ingest rather than at startup.

        An implementation that has to ask the provider may do so once and
        cache: callers read this per ingest, not per chunk.
        """
        raise NotImplementedError

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    def embed_batch(self, texts: list[str], *, batch_size: int | None = None) -> list[list[float]]:
        raise NotImplementedError


class BGEEmbedder(Embedder):
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    @property
    def dim(self) -> int:
        # Asked of the LOADED model, not parsed from EMBED_MODEL's name: the
        # name is a string someone can mistype, the model is the thing that
        # will actually produce the vectors.
        #
        # get_embedding_dimension, not get_sentence_embedding_dimension. The
        # latter was renamed in sentence-transformers 5.4.0 and survives only as
        # a FutureWarning-decorated alias - scheduled for removal, and when it
        # goes, ingestion goes with it, because flow.py sizes the collection
        # from this property. Verified against the released wheels, which is
        # also why pyproject.toml floors the dependency at 5.4.
        dim = self._model.get_embedding_dimension()
        if dim is None:
            # The library returns None when a model has no fixed output size.
            # A collection is created at one exact length, so there is no safe
            # default here - and int(None) would fail with a TypeError that says
            # nothing about the cause.
            raise ValueError(
                "The loaded embedding model reports no fixed embedding dimension, "
                "so a vector collection cannot be sized for it."
            )
        return int(dim)

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str], *, batch_size: int | None = None) -> list[list[float]]:
        """Embed many texts in one call.

        batch_size is exposed because the caller knows how much work it has:
        feeding ~2 chunks at a time (one short article) leaves the device idle
        between calls, and per-call overhead dominates. sentence-transformers
        defaults to 32.
        """
        return self._model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=batch_size or 32,
        ).tolist()
