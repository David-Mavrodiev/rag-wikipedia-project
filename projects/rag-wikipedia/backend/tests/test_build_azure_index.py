"""The ACL demo index: deterministic groups, and a schema that can be filtered.

If the group assignment were random, a test of the trimming filter would be
testing the dice. If `acl_groups` were not filterable, the predicate could not
run in the index and the only way to apply it would be the leaky one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from build_azure_index import (  # noqa: E402
    GROUPS,
    VECTOR_DIM,
    assign_groups,
    group_split,
    index_schema,
)

# --- the groups ---------------------------------------------------------------

def test_the_same_article_always_lands_in_the_same_groups():
    assert assign_groups("Biotite") == assign_groups("Biotite")


def test_every_article_gets_at_least_one_group():
    # A chunk with no groups would be invisible to everyone, which looks like
    # trimming working when it is really a bug.
    for title in ("Biotite", "Agate", "Kwadacha", "", "Île-de-France"):
        assert assign_groups(title)


def test_only_known_groups_are_assigned():
    titles = [f"Article {index}" for index in range(300)]
    used = {group for title in titles for group in assign_groups(title)}
    assert used <= set(GROUPS)


def test_most_of_the_corpus_is_public_but_not_all_of_it():
    titles = [f"Article {index}" for index in range(500)]
    documents = [{"acl_groups": assign_groups(title)} for title in titles]
    split = group_split(documents)

    assert split["public"] > len(titles) / 2, "most chunks should be readable by anyone"
    assert split["public"] < len(titles), "something must be restricted or nothing is trimmed"
    assert split.get("research", 0) and split.get("legal", 0)


# --- the schema ---------------------------------------------------------------

def test_the_acl_field_is_filterable_or_trimming_cannot_run_in_the_index():
    fields = {field["name"]: field for field in index_schema("i")["fields"]}
    assert fields["acl_groups"]["type"] == "Collection(Edm.String)"
    assert fields["acl_groups"]["filterable"] is True


def test_the_vector_field_matches_the_embedder_this_project_uses():
    vector = next(f for f in index_schema("i")["fields"] if f["name"] == "vector")
    assert vector["dimensions"] == VECTOR_DIM == 384
    assert vector["vectorSearchProfile"] == "hnsw-cosine"


def test_the_index_carries_exactly_the_fields_the_store_reads():
    # AzureSearchStore.search() reads these four plus the score.
    names = {field["name"] for field in index_schema("i")["fields"]}
    assert {"id", "text", "title", "source_id", "acl_groups", "vector"} == names


def test_cosine_because_that_is_what_the_corpus_was_built_with():
    schema = index_schema("i")
    assert schema["vectorSearch"]["algorithms"][0]["hnswParameters"]["metric"] == "cosine"
