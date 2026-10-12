"""What a vector store must do, as a Protocol rather than a base class.

`retrieve()` calls exactly one method on a store - `search` - and ingestion
calls four more. Writing that down as a `typing.Protocol` makes a second store
possible without touching the first: **structural typing means `QdrantStore`
already satisfies this, unmodified**. That matters beyond tidiness here,
because `vectorstore.py` is watched by `audit_freshness`, and editing it forces
a re-measured audit of a corpus a type hint cannot affect.

A store answers with plain dicts (`score`, `text`, `title`, `source_id`) so
that `retrieve()`, the refusal gate and the citation builder keep one shape to
reason about whatever is underneath.

WHAT A STORE IS NOT ALLOWED TO DO. It does not rank beyond what its index does,
it does not decide what counts as sufficient evidence, and it does not trim
results in Python for access control - a filter that runs after the search has
already leaked which documents exist, through counts and scores. An
access-controlled store pushes the predicate into the query it sends.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class VectorStore(Protocol):
    """The surface the serving path and the ingestion pipeline rely on."""

    def search(self, vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        """Nearest neighbours, best first, as `{score, text, title, source_id}`."""
        ...

    def count(self) -> int:
        """How many vectors are stored - the number every report records."""
        ...


@runtime_checkable
class WritableVectorStore(VectorStore, Protocol):
    """A store the ingestion pipeline can fill.

    Separate from `VectorStore` because serving needs none of it: a read-only
    replica, or a managed index someone else fills, is a perfectly good store
    for answering questions.
    """

    def ensure_collection(self, dim: int) -> None:
        """Create the collection at *dim*, or verify the existing one agrees."""
        ...

    def upsert_batch(self, points: list[dict[str, Any]]) -> None:
        ...

    def existing_ids(self, point_ids: list[str]) -> set[str]:
        """The subset already stored - what makes ingestion resumable."""
        ...
