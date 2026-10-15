"""Azure AI Search as a second store, with access control that actually trims.

Selected with `STORE_CHOICE=azure-search`. Everything the serving path needs is
`search()`; the rest exists so an index can be built and checked.

THE ONE RULE THIS FILE EXISTS FOR. The permission predicate goes INTO the query
sent to Azure. It is never applied to results after they come back. A Python
filter over returned hits still leaks what it hides: the result count changes,
the scores shift, and a caller can tell that a document they may not read
exists and matches. "Security trimming" means the index never returns the row.

HOW A CALLER'S GROUPS GET HERE. They are bound when the store is BUILT, once
per request, in `app/api/query.py`. That keeps `retrieve()` - a watched file -
at its exact signature: it still calls `store.search(vector, top_k=...)` and
knows nothing about principals. A store that carries its caller's groups is
also harder to misuse than a search() with an optional `groups=None` argument
that defaults to seeing everything.

IT FAILS CLOSED. No groups means no documents, and that is a test, not a
comment. The opposite default - no groups means everything - is how
access-controlled search goes wrong in production.

FREE TIER. Azure AI Search's free tier is 50 MB and 3 indexes, which holds a
few thousand chunks, not 87,173. This is a capability demo on a subset, and the
reports say which corpus they describe (scripts/build_azure_index.py).

WHERE THIS LIVES. In `providers/`, beside the Vertex adapters, not under
`app/`: tests/test_layering.py walks the AST and forbids a cloud SDK anywhere
in the core, lazily imported or not. `app.core.stores` owns the Protocol and
the registry; the SDK lives out here. The import is still inside the methods
that use it, so `import providers` works where the `azure` extra is not
installed - which includes CI.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# A group name is an identifier, not free text. Anything else is refused rather
# than escaped: this string is going into an OData filter, and a quote in a
# group name is either a bug or an attempt to rewrite the predicate.
_GROUP = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")

VECTOR_FIELD = "vector"
ACL_FIELD = "acl_groups"


class AclError(ValueError):
    """A group name that cannot be put in a filter safely."""


def acl_filter(groups: list[str], field: str = ACL_FIELD) -> str:
    """The OData predicate for "any of the caller's groups is on the document".

    Raises on a group name that is not an identifier - see _GROUP.
    """
    for group in groups:
        if not _GROUP.match(group):
            raise AclError(f"refusing to build a filter from group {group!r}")
    joined = ",".join(groups)
    return f"{field}/any(g: search.in(g, '{joined}', ','))"


class AzureSearchStore:
    """A `VectorStore` whose every query carries its caller's permissions."""

    def __init__(self, endpoint: str, index: str, *, groups: list[str] | None = None,
                 api_key: str | None = None, credential: Any = None,
                 vector_field: str = VECTOR_FIELD, acl_field: str = ACL_FIELD) -> None:
        self._endpoint = endpoint
        self._index = index
        # None and [] mean the same thing here, and that thing is "nothing".
        self._groups = list(groups or [])
        self._api_key = api_key
        self._credential = credential
        self._vector_field = vector_field
        self._acl_field = acl_field
        self._client: Any = None

    # --- the client -----------------------------------------------------------

    def _search_client(self) -> Any:
        if self._client is not None:
            return self._client
        from azure.search.documents import SearchClient  # noqa: PLC0415

        credential = self._credential
        if credential is None:
            if self._api_key:
                from azure.core.credentials import AzureKeyCredential  # noqa: PLC0415

                credential = AzureKeyCredential(self._api_key)
            else:
                # Managed identity in Azure, `az login` locally: no key to leak.
                from azure.identity import DefaultAzureCredential  # noqa: PLC0415

                credential = DefaultAzureCredential()
        self._client = SearchClient(endpoint=self._endpoint, index_name=self._index,
                                    credential=credential)
        return self._client

    # --- the VectorStore surface ----------------------------------------------

    def search(self, vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        """Nearest neighbours the caller is allowed to see, best first."""
        if not self._groups:
            # Fail closed. Not an error: an unauthenticated visitor legitimately
            # sees nothing, and the refusal gate upstream turns that into an
            # honest "I don't know" rather than an exception.
            logger.info("azure-search: no groups on this request, returning nothing")
            return []

        from azure.search.documents.models import VectorizedQuery  # noqa: PLC0415

        query = VectorizedQuery(vector=vector, k_nearest_neighbors=top_k,
                                fields=self._vector_field)
        results = self._search_client().search(
            search_text=None,
            vector_queries=[query],
            # The predicate travels WITH the query. This is the whole file.
            filter=acl_filter(self._groups, self._acl_field),
            top=top_k,
        )
        return [
            {
                "score": document.get("@search.score", 0.0),
                "text": document.get("text", ""),
                "title": document.get("title", ""),
                "source_id": document.get("source_id", ""),
            }
            for document in results
        ]

    def count(self) -> int:
        """Documents in the index - the number a report records for this corpus."""
        return int(self._search_client().get_document_count())
