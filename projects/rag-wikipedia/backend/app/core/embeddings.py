from __future__ import annotations

from abc import ABC, abstractmethod


class Embedder(ABC):
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
