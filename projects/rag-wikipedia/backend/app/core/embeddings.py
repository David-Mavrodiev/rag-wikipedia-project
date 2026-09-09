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
        return int(self._model.get_sentence_embedding_dimension())

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
