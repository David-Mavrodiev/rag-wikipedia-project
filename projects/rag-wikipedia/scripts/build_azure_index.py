"""Build the ACL demo index on Azure AI Search, on the free tier.

    python scripts/build_azure_index.py --dry-run          # schema + group split, no Azure
    python scripts/build_azure_index.py --limit 4000       # create and fill

The free tier is 50 MB and 3 indexes, so this is a SUBSET - a few thousand
chunks of the same corpus - and every report built on it says so. It exists to
show security trimming working, not to host the 87,173 chunks the published
numbers describe.

THE GROUPS ARE SYNTHETIC, and this file is the only place that says how they
are decided: Wikipedia has no access control, so one is invented, deterministic
from the article title, with most chunks public and two restricted groups. That
is honest about what is being demonstrated - the FILTER, not a permission
model.

Reads from the Qdrant collection that already holds the corpus, so nothing is
re-embedded: the vectors are copied as they are.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

VECTOR_DIM = 384
BATCH = 500
# Most of the corpus is readable by anyone; two groups exist so that trimming
# has something to trim. The shares are a demo choice, not a measurement.
GROUPS = ("public", "research", "legal")


def assign_groups(title: str) -> list[str]:
    """Which groups may read this article. Deterministic, from the title alone.

    Deterministic matters: the same subset rebuilt twice must trim identically,
    or a test of the filter is testing the dice instead.
    """
    digest = hashlib.sha1(title.encode("utf-8")).digest()[0]
    if digest % 10 < 7:
        return ["public"]
    if digest % 10 < 9:
        return ["research"]
    return ["legal", "research"]


def index_schema(name: str, dim: int = VECTOR_DIM) -> dict[str, Any]:
    """The index definition, as plain data so it can be read in a test."""
    return {
        "name": name,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "text", "type": "Edm.String", "searchable": True},
            {"name": "title", "type": "Edm.String", "searchable": True, "filterable": True},
            {"name": "source_id", "type": "Edm.String", "filterable": True},
            # The ACL field: filterable, because the whole point is that the
            # predicate runs in the index rather than in our process.
            {"name": "acl_groups", "type": "Collection(Edm.String)", "filterable": True},
            {"name": "vector", "type": "Collection(Edm.Single)", "searchable": True,
             "dimensions": dim, "vectorSearchProfile": "hnsw-cosine"},
        ],
        "vectorSearch": {
            "algorithms": [{"name": "hnsw", "kind": "hnsw",
                            "hnswParameters": {"metric": "cosine"}}],
            "profiles": [{"name": "hnsw-cosine", "algorithm": "hnsw"}],
        },
    }


def documents_from_qdrant(url: str, collection: str, limit: int) -> list[dict[str, Any]]:
    """Copy chunks and their vectors out of Qdrant - nothing is re-embedded."""
    from qdrant_client import QdrantClient

    client = QdrantClient(url=url, timeout=300)
    documents: list[dict[str, Any]] = []
    offset = None
    while len(documents) < limit:
        points, offset = client.scroll(
            collection_name=collection, limit=min(BATCH, limit - len(documents)),
            offset=offset, with_payload=True, with_vectors=True,
        )
        for point in points:
            payload = point.payload or {}
            title = payload.get("title", "")
            documents.append({
                "id": str(point.id),
                "text": payload.get("text", ""),
                "title": title,
                "source_id": str(payload.get("source_id", "")),
                "acl_groups": assign_groups(title),
                "vector": list(point.vector) if point.vector else [],
            })
        if offset is None:
            break
    return documents[:limit]


def group_split(documents: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for document in documents:
        for group in document["acl_groups"]:
            counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL",
                                                               "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("COLLECTION", "wikipedia"))
    parser.add_argument("--endpoint", default=os.environ.get("AZURE_SEARCH_ENDPOINT", ""))
    parser.add_argument("--index", default=os.environ.get("AZURE_SEARCH_INDEX",
                                                          "wikipedia-subset"))
    parser.add_argument("--api-key", default=os.environ.get("AZURE_SEARCH_API_KEY"))
    parser.add_argument("--limit", type=int, default=4000,
                        help="free tier is 50 MB: a few thousand chunks, not 87,173")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the schema and the group split; touch no cloud")
    args = parser.parse_args(argv)

    documents = documents_from_qdrant(args.qdrant_url, args.collection, args.limit)
    print(f"{len(documents)} chunks from `{args.collection}`")
    print(f"group split: {group_split(documents)}")

    if args.dry_run:
        import json

        print(json.dumps(index_schema(args.index), indent=2)[:1200])
        print("dry run: nothing was created")
        return 0

    if not args.endpoint:
        print("no --endpoint (or AZURE_SEARCH_ENDPOINT): nothing to upload to", file=sys.stderr)
        return 2

    from azure.core.credentials import AzureKeyCredential
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    credential = AzureKeyCredential(args.api_key) if args.api_key else DefaultAzureCredential()
    SearchIndexClient(args.endpoint, credential).create_or_update_index(
        index_schema(args.index))
    client = SearchClient(args.endpoint, args.index, credential)
    for start in range(0, len(documents), BATCH):
        client.upload_documents(documents[start:start + BATCH])
        print(f"  uploaded {min(start + BATCH, len(documents))}/{len(documents)}")
    print(f"index `{args.index}` ready; set STORE_CHOICE=azure-search to use it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
