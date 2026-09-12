# RAG over Wikipedia — Annotated Test Inventory

> Current snapshot: **332 backend pytest tests** across 30 files, plus **11 frontend
> Vitest component tests** across 4 files. The original backend annotations below are
> kept as teaching notes; the current audit sections call out tests added since this
> file was first written. Where a file has grown since it was annotated, the
> annotation shows the original tests only — the audit below is the authoritative
> inventory.
> Companion to `ANNOTATED_CODE.md` (the source). Run backend tests with `make test`
> from `projects/rag-wikipedia`.

## Testing philosophy (what a reviewer looks for)

The suite mixes three kinds of test on purpose:

| Kind | What it does | Files |
|---|---|---|
| **Pure unit** | Tests deterministic logic without network, models, or stores | `test_chunking`, `test_config`, `test_generation`, `test_metrics`, `test_runtime_config`, `test_llm`, refusal parsing in `test_refusal` |
| **Mocked wiring** | Tests API/retrieval/pipeline/eval flow by faking slow dependencies | `test_retrieval`, `test_api`, `test_pipeline`, `test_run_eval`, `test_rate_limit`, `test_quality`, `test_startup_config`, `test_audit`, API cases in `test_refusal` |
| **Contract / integration-lite** | Uses real in-process components where mocks previously hid risk | `test_vectorstore` with in-memory Qdrant, FastAPI `TestClient` route tests |
| **Eval coverage** | Protects RAG quality gates and groundedness opt-in behavior | `test_run_eval`, `test_metrics`, `test_audit` |
| **Frontend component** | Verifies presentational/query components with Vitest and React Testing Library | `AnswerView.test.tsx`, `CitationList.test.tsx`, `QueryBox.test.tsx`, `QualityPanel.test.tsx` |

The credibility-critical paths get the most coverage: **refusal logic, citation extraction, chunking determinism, the query path, eval gates, and the Qdrant contract.** The contract tests exist because mocking the vector store is *exactly* what let a real client API break (`.search()` removed) reach production once — so those tests now run against the real thing.

---

## `backend/tests/conftest.py` — shared fixtures

```python
import pytest                                    # the test framework
from app.main import app                          # the real FastAPI app (routes + middleware)
from fastapi.testclient import TestClient         # spins up the app in-process, no network needed


@pytest.fixture                                   # a fixture: any test that names `client` gets this value
def client():
    return TestClient(app)                        # a test HTTP client wired to our app (used by test_api/test_health)
```

---

## `backend/tests/test_config.py` — settings load and env overrides

```python
def test_defaults():                              # the defaults are what we expect
    from app.core.config import Settings          # import inside the test so env changes elsewhere don't leak in
    settings = Settings()                          # build a fresh Settings (reads env / .env)
    assert settings.qdrant_url == "http://localhost:6333"  # default Qdrant URL
    assert settings.top_k == 5                     # default retrieval depth
    assert settings.profile == "tiny"             # default ingestion profile


def test_env_override(monkeypatch):               # environment variables override the defaults
    monkeypatch.setenv("TOP_K", "10")             # monkeypatch temporarily sets an env var, auto-undone after the test
    monkeypatch.setenv("PROFILE", "real")
    from app.core.config import Settings
    settings = Settings()                          # rebuild: should now pick up the env vars
    assert settings.top_k == 10                   # int is parsed from the string "10"
    assert settings.profile == "real"
```

---

## `backend/tests/test_chunking.py` — chunking + markup cleaning

```python
from app.core.chunking import chunk_text          # the function under test


def test_chunk_single_sentence():                 # a short text produces exactly one chunk
    text = "Hello world."
    chunks = chunk_text(text, "art_001")          # source_id = "art_001"
    assert len(chunks) == 1                       # one chunk
    assert chunks[0].text == "Hello world."       # text preserved (round-trips through the tokenizer)
    assert chunks[0].source_id == "art_001"       # source id carried through
    assert chunks[0].chunk_index == 0             # first (and only) chunk is index 0


def test_chunk_deterministic_ids():               # THE key property: same input → same ids (idempotency)
    text = "A " * 600                             # long enough to make several chunks
    chunks1 = chunk_text(text, "art_001")
    chunks2 = chunk_text(text, "art_001")         # chunk the SAME text again
    assert [c.point_id for c in chunks1] == [c.point_id for c in chunks2]  # identical ids both times


def test_chunk_overlap_produces_multiple():       # a long text splits into multiple chunks
    text = "word " * 700                          # >512 tokens → more than one chunk
    chunks = chunk_text(text, "art_002")
    assert len(chunks) >= 2


def test_chunk_different_sources_different_ids():  # same text, different articles → different ids
    text = "word " * 600
    chunks_a = chunk_text(text, "art_a")
    chunks_b = chunk_text(text, "art_b")
    assert chunks_a[0].point_id != chunks_b[0].point_id  # source_id is part of the id, so they differ


def test_clean_normalize():                       # the markup cleaner strips wiki syntax but keeps content
    from pipeline.tasks import clean_text          # the function under test (lives in the pipeline)
    raw = "==Section==\n[[Link|Text]] {{template}} <b>bold</b> content"  # messy wiki markup
    cleaned = clean_text(raw)
    assert "{{" not in cleaned                    # templates removed
    assert "[[" not in cleaned                    # link syntax removed
    assert "<b>" not in cleaned                   # html tags removed
    assert "content" in cleaned                   # actual prose kept
```

---

## `backend/tests/test_generation.py` — prompt assembly + citation extraction

```python
from app.core.citations import build_citations, extract_citation_indices  # functions under test
from app.core.prompt import build_prompt, build_refusal_prompt

MOCK_CHUNKS = [                                    # two fake retrieved chunks reused across tests
    {"text": "Python is a programming language.", "title": "Python", "source_id": "1"},
    {"text": "It was created by Guido van Rossum.", "title": "Python", "source_id": "1"},
]


def test_build_prompt_includes_citations():       # the prompt numbers chunks [1], [2] and includes the question
    prompt = build_prompt("What is Python?", MOCK_CHUNKS)
    assert "[1]" in prompt                        # first chunk labelled [1]
    assert "[2]" in prompt                        # second chunk labelled [2]
    assert "Python is a programming language" in prompt  # chunk text is embedded
    assert "What is Python?" in prompt            # the question is embedded


def test_build_refusal_prompt():                  # the no-context prompt still carries the refusal instruction
    prompt = build_refusal_prompt("What is Python?")
    assert "I don't know" in prompt or "none" in prompt.lower()


def test_extract_citation_indices():              # extracting [n] markers dedupes and sorts
    answer = "Python [1] was created by Guido [2]. See also [1] for details."
    indices = extract_citation_indices(answer)
    assert indices == [1, 2]                      # [1] appears twice but is counted once, sorted ascending


def test_extract_no_citations():                  # a refusal answer has no [n] markers
    answer = "I don't know based on the provided context."
    assert extract_citation_indices(answer) == []  # empty list


def test_build_citations():                       # mapping cited numbers back to sources
    cited = build_citations(MOCK_CHUNKS, [1, 2])
    assert len(cited) == 2                        # both citations built
    assert cited[0]["title"] == "Python"          # metadata attached
    assert cited[0]["index"] == 1                 # index preserved


def test_build_citations_out_of_range():          # a hallucinated number is dropped, not crashed on
    cited = build_citations(MOCK_CHUNKS, [1, 99]) # [99] doesn't exist (only 2 chunks)
    assert len(cited) == 1                        # only the valid [1] survives
```

---

## `backend/tests/test_retrieval.py` — the token budget + refusal logic (mocked deps)

```python
from unittest.mock import MagicMock               # a stand-in object whose methods return whatever we tell them


def test_token_budget_truncation():               # a small budget cuts off later chunks
    from app.core.retrieval import retrieve
    embedder = MagicMock()                        # fake embedder…
    embedder.embed.return_value = [0.1] * 384     # …that returns a dummy 384-d vector
    store = MagicMock()                           # fake store…
    store.search.return_value = [                 # …that returns 3 big chunks (each ~1000 words)
        {"score": 0.9, "text": "word " * 1000, "title": "A", "source_id": "1"},
        {"score": 0.85, "text": "word " * 1000, "title": "B", "source_id": "2"},
        {"score": 0.8, "text": "word " * 1000, "title": "C", "source_id": "3"},
    ]
    chunks, empty = retrieve("test query", embedder, store, top_k=3, token_budget=500)  # tiny 500-token budget
    assert len(chunks) <= 2                        # the budget stops us well before all 3 chunks
    assert not empty                              # we kept the top chunk, so it's not a refusal


def test_returns_empty_when_no_results():         # nothing found → refuse
    from app.core.retrieval import retrieve
    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384
    store = MagicMock()
    store.search.return_value = []                # Qdrant returned nothing
    chunks, empty = retrieve("test query", embedder, store)
    assert chunks == []                           # no chunks
    assert empty is True                          # refusal signal


def test_refusal_on_low_score():                  # best hit below the threshold → refuse
    from app.core.retrieval import retrieve
    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 384
    store = MagicMock()
    store.search.return_value = [                 # a hit, but score 0.01 (< 0.3 refusal_threshold)
        {"score": 0.01, "text": "some text", "title": "X", "source_id": "1"},
    ]
    _, empty = retrieve("test query", embedder, store)
    assert empty is True                          # weak match is treated as "no relevant context"
```

---

## `backend/tests/test_vectorstore.py` — CONTRACT tests against a real in-memory Qdrant

```python
from __future__ import annotations
import pytest
from app.core.vectorstore import QdrantStore
from qdrant_client import QdrantClient            # the REAL client, run in ":memory:" mode (no server needed)


@pytest.fixture
def store() -> QdrantStore:
    store = QdrantStore.__new__(QdrantStore)      # build the object WITHOUT calling __init__ (skip the network connect)…
    store._client = QdrantClient(":memory:")      # …and inject a real, in-process Qdrant instead
    store._collection = "test"
    store.ensure_collection()                     # create the 384-d cosine collection
    return store


def _vector(seed: float) -> list[float]:          # helper: a 384-d vector filled with one value
    return [seed] * 384


def test_upsert_then_search_returns_payload(store: QdrantStore):  # write one point, read it back
    store.upsert_batch([{"id": 1, "vector": _vector(0.9),
                         "payload": {"text": "Python is a language", "title": "Python", "source_id": "a"}}])
    results = store.search(_vector(0.9), top_k=1) # search with the SAME vector → should return that point
    assert len(results) == 1                      # one hit
    assert results[0]["text"] == "Python is a language"   # payload round-trips
    assert results[0]["title"] == "Python"
    assert results[0]["source_id"] == "a"
    assert results[0]["score"] == pytest.approx(1.0, abs=1e-3)  # identical vectors ⇒ cosine ≈ 1.0 (allow float slack)


def test_search_respects_top_k(store: QdrantStore):  # top_k limits the number of results
    store.upsert_batch([{"id": i, "vector": _vector(0.1 * i), "payload": {"text": f"chunk {i}"}} for i in range(1, 6)])
    assert len(store.search(_vector(0.3), top_k=2)) == 2  # 5 points stored, but only 2 requested


def test_search_on_empty_collection_returns_empty(store: QdrantStore):  # empty collection → no hits
    assert store.search(_vector(0.5), top_k=5) == []


def test_count_reflects_upserts(store: QdrantStore):  # count() tracks how many points are stored
    assert store.count() == 0                     # starts empty
    store.upsert_batch([{"id": 1, "vector": _vector(0.2), "payload": {"text": "x"}}])
    assert store.count() == 1                     # one after inserting one


def test_ensure_collection_rejects_wrong_dim(store: QdrantStore):  # the dimension guard fires
    with pytest.raises(ValueError, match="Expected embedding dim"):  # expect a ValueError with this message
        store.ensure_collection(dim=768)          # 768 != 384 → guard raises
```

> **Why this file matters (say this in an interview):** every other test fakes the store with a mock, so they'd pass even if the real Qdrant client's API changed. These run against the *real* client (in-memory), so a breaking client/DB change fails here instead of in production. That's the lesson from the `503` incident.

---

## `backend/tests/test_metrics.py` — the eval metric functions

```python
from eval.metrics import groundedness, mrr, recall_at_k, reciprocal_rank, refusal_accuracy


def test_recall_at_k_full_match():                # all expected keywords present → recall 1.0
    texts = ["Python is a language", "ML is AI"]
    assert recall_at_k(texts, ["Python", "language"], k=2) == 1.0


def test_recall_at_k_partial():                   # some keywords present → between 0 and 1
    texts = ["Python is a language", "unrelated text"]
    score = recall_at_k(texts, ["Python", "Java"], k=2)  # "Python" found, "Java" not
    assert 0.0 < score < 1.0                      # exactly 0.5, but assert the range for robustness


def test_recall_at_k_no_match():                  # no keywords present → 0.0
    texts = ["unrelated text"]
    assert recall_at_k(texts, ["Python"], k=1) == 0.0


def test_recall_at_k_empty_keywords():            # no keywords given → defined as 0.0 (avoid divide-by-zero)
    assert recall_at_k(["text"], [], k=1) == 0.0


def test_reciprocal_rank_first():                 # relevant chunk is rank 1 → 1/1 = 1.0
    texts = ["Python is a language", "unrelated"]
    assert reciprocal_rank(texts, ["Python"]) == 1.0


def test_reciprocal_rank_second():                # relevant chunk is rank 2 → 1/2 = 0.5
    texts = ["unrelated", "Python is a language"]
    assert reciprocal_rank(texts, ["Python"]) == 0.5


def test_reciprocal_rank_none():                  # no relevant chunk → 0.0
    texts = ["unrelated"]
    assert reciprocal_rank(texts, ["Python"]) == 0.0


def test_mrr_calculation():                       # MRR = mean of the reciprocal ranks
    scores = [1.0, 0.5, 0.0]
    result = mrr(scores)
    assert abs(result - (1.0 + 0.5 + 0.0) / 3) < 1e-9  # float-safe equality (don't compare floats with ==)


def test_mrr_empty():                             # no scores → 0.0
    assert mrr([]) == 0.0


def test_refusal_accuracy_all_refused():          # all correctly refused → 1.0
    assert refusal_accuracy([True, True, True]) == 1.0


def test_refusal_accuracy_partial():              # half refused → 0.5
    assert refusal_accuracy([True, False, True, False]) == 0.5


def test_refusal_accuracy_empty():                # no cases → 0.0
    assert refusal_accuracy([]) == 0.0


def test_groundedness_full():                     # answer words all appear in context → high score
    answer = "Python language"
    context = ["Python is a language"]
    score = groundedness(answer, context)
    assert score > 0.5


def test_groundedness_empty_answer():             # empty answer → 0.0
    assert groundedness("", ["some context"]) == 0.0


def test_groundedness_empty_context():            # no context → 0.0
    assert groundedness("some answer", []) == 0.0
```

---

## `backend/tests/test_pipeline.py` — ingestion is idempotent

```python
from unittest.mock import MagicMock

FIXTURE_ARTICLES = [                              # two fake articles to ingest (repeated text to force chunking)
    {"id": "1", "title": "Python programming", "text": "Python is a programming language. " * 50},
    {"id": "2", "title": "Machine learning", "text": "Machine learning is a subset of AI. " * 50},
]


def _make_store():                                # build a fake QdrantStore that records upserts in a dict
    store = MagicMock()
    store.upsert_batch = MagicMock()
    store.ensure_collection = MagicMock()
    storage = {}                                  # dict keyed by point id → simulates the DB

    def fake_upsert(points):                      # our fake upsert: write each point by id (so re-writes overwrite)
        for point in points:
            storage[point["id"]] = point

    store.upsert_batch.side_effect = fake_upsert  # when upsert_batch is called, run fake_upsert
    store._storage = storage                       # expose the dict so the test can count points
    return store


def _make_embedder():                             # fake embedder: returns a dummy 384-d vector per text
    embedder = MagicMock()
    embedder.embed_batch.side_effect = lambda texts: [[0.1] * 384 for _ in texts]
    return embedder


def test_idempotency():                           # re-running ingestion must NOT change the vector count
    """Re-running the pipeline on the same articles should not change vector count."""
    from pipeline.tasks import chunk_article, clean_article, embed_chunks, upsert_to_qdrant

    store = _make_store()
    embedder = _make_embedder()

    def run_pipeline():                           # run the 4 steps for each article
        for article in FIXTURE_ARTICLES:
            cleaned = clean_article.fn(article)   # .fn calls the raw function behind the Prefect @task wrapper
            chunks = chunk_article.fn(cleaned)
            chunk_vectors = embed_chunks.fn(chunks, embedder)
            upsert_to_qdrant.fn(chunk_vectors, store, cleaned)

    run_pipeline()                                # first ingestion
    count_after_first = len(store._storage)       # how many unique point ids were written
    run_pipeline()                                # second ingestion of the SAME articles
    count_after_second = len(store._storage)

    assert count_after_first == count_after_second, "Re-run changed vector count — not idempotent"  # deterministic ids ⇒ overwrite
    assert count_after_first > 0                  # sanity: we actually wrote something
```

---

## `backend/tests/test_health.py` — the liveness endpoint

```python
def test_health_returns_200(client):             # `client` fixture comes from conftest.py
    response = client.get("/health")             # call GET /health through the in-process test client
    assert response.status_code == 200           # up
    assert response.json() == {"status": "ok"}   # exact body
```

---

## `backend/tests/test_api.py` — the query path end-to-end (deps mocked)

```python
from unittest.mock import patch                   # patch() temporarily replaces an object during a `with` block

from app.core.refusal import REFUSAL_MESSAGE      # the single-source refusal string (see refusal.py)


def test_health(client):                          # /health returns 200 (smoke test of the app + client)
    response = client.get("/health")
    assert response.status_code == 200


def test_query_empty_question(client):            # an empty question is rejected by Pydantic validation
    response = client.post("/query", json={"question": ""})
    assert response.status_code == 422            # 422 = validation error (min_length=1)


def test_query_too_long(client):                  # a >500-char question is rejected too
    response = client.post("/query", json={"question": "x" * 501})
    assert response.status_code == 422            # 422 = validation error (max_length=500)


# NOTE: this assertion used to be `"don't know" in answer OR citations == []`.
# The OR made it pass for ANY answer with no citations — including a perfectly
# good grounded answer the model forgot to cite. That is exactly the bug the
# `refused` flag exists to prevent, so the test asserts the explicit state.
# tests/test_refusal.py covers the full matrix (hard refusal, soft refusal,
# grounded-with-citations, grounded-without-citations).
def test_query_returns_refusal_when_no_context(client):  # empty retrieval → refusal, LLM not needed
    with (                                        # replace the three lru_cache singletons with mocks for this test
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm"),              # llm patched but unused (refusal skips it)
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384  # embedder() → object → .embed() → dummy vector
        mock_store.return_value.search.return_value = []             # store() → object → .search() → nothing found
        response = client.post("/query", json={"question": "What is quantum gravity?"})

    assert response.status_code == 200            # refusal is a normal 200 response, not an error
    data = response.json()
    assert data["refused"] is True                # EXPLICIT state, not inferred
    assert data["answer"] == REFUSAL_MESSAGE      # the exact contract string
    assert data["citations"] == []                # a refusal never cites


def test_query_returns_answer_with_citations(client):  # a good retrieval → grounded answer + citations
    mock_chunks = [                               # one fake chunk that the fake store will "find"
        {"score": 0.95, "text": "Python is a programming language [1].", "title": "Python", "source_id": "1"}
    ]
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store") as mock_store,
        patch("app.api.query._llm") as mock_llm,
    ):
        mock_embedder.return_value.embed.return_value = [0.1] * 384
        mock_store.return_value.search.return_value = mock_chunks    # store returns our chunk (score 0.95 > 0.3)
        mock_llm.return_value.generate.return_value = "Python [1] is great."  # fake LLM answer that cites [1]
        response = client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 200
    data = response.json()
    assert "answer" in data                       # response has an answer field
    assert "citations" in data                    # …and a citations field


def test_query_qdrant_down(client):               # a retrieval exception → 503 (dependency failure mapping)
    with (
        patch("app.api.query._embedder") as mock_embedder,
        patch("app.api.query._store"),
    ):
        mock_embedder.return_value.embed.side_effect = Exception("connection refused")  # embed() blows up
        response = client.post("/query", json={"question": "What is Python?"})

    assert response.status_code == 503            # the API maps the failure to 503, not a 500 crash
```

---

## Current coverage audit

<!-- docs-check:begin test-inventory -->
Backend: **332 tests** across 30 files.

```text
test_refusal.py          56
test_idf.py              26
test_run_eval.py         22
test_metrics_latency.py  19
test_quality.py          19
test_runtime_config.py   18
test_config.py           15
test_metrics.py          15
test_metrics_endpoint.py 11
test_engines.py          10
test_holdout.py          9
test_llm.py              9
test_compare_audit.py    8
test_langgraph_engine.py 8
test_vectorstore.py      8
test_embeddings.py       7
test_api.py              6
test_bench_ingest.py     6
test_compare_engines.py  6
test_engine_conformance.py 6
test_generation.py       6
test_layering.py         6
test_retrieval.py        6
test_audit.py            5
test_chunking.py         5
test_pipeline.py         5
test_startup_config.py   5
test_stream_resilience.py 5
test_rate_limit.py       4
test_health.py           1
```

Frontend: **11 tests** across 4 files.

```text
AnswerView.test.tsx      1
CitationList.test.tsx    3
QualityPanel.test.tsx    4
QueryBox.test.tsx        3
```
<!-- docs-check:end -->

This project does **not** currently configure numeric line or branch coverage. Treat
the counts above as test inventory and behavioral coverage, not as a percentage claim.

---

## Tests added after the original annotation

`backend/tests/test_bench_ingest.py` protects the ingestion benchmark from writing
into the primary collection by accident. It verifies throwaway collection naming,
explicit collection overrides, successful collection claiming, duplicate-name refusal,
and reraising genuine Qdrant failures.

`backend/tests/test_refusal.py` is the refusal-contract regression suite. It proves
that exact and soft refusals are marked as refused, grounded answers remain accepted
even without `[n]` markers, curly quotes/apostrophes are normalized, and the API
separates hard retrieval refusals from soft LLM refusals.

`backend/tests/test_run_eval.py` covers the eval harness behavior added after the
original 44-test snapshot. It verifies that groundedness is reported only when an LLM
is supplied, the fast path never calls the LLM, the CLI flag defaults off, and
groundedness remains report-only rather than part of the pass/fail gate. It also pins
the scoring bug where recall matched titles casefolded while precision compared them
raw, so a corpus title whose case differed from `golden.jsonl` scored recall 1.0 and
precision 0.0 for the same case.

`backend/tests/test_rate_limit.py` covers the Redis token-bucket middleware: 429 once
the bucket is empty, rejection *before* any expensive query work runs, `/health` left
unlimited, and a fail-closed 503 when Redis is unreachable rather than silently
serving unlimited traffic.

`backend/tests/test_quality.py` covers the `/quality` surface. The audit is dispatched
to a worker thread rather than awaited on the event loop, a second concurrent audit
gets 409 instead of interleaving mutations of the shared runtime config, auto-correction
is refused without an admin token, a truncated or non-object `audit_report.json`
degrades to `unknown` instead of raising a 500, and POST returns the same response
shape as GET so the dashboard does not blank out after an audit.

`backend/tests/test_runtime_config.py` covers the persisted retrieval config: bounds
validation on every field, an absolute config path that does not depend on the process
working directory, atomic writes that leave no temp files and never expose a partial
read, and rejection of missing keys, unknown-key tolerance, and non-object files.

`backend/tests/test_startup_config.py` covers boot behavior: a persisted config is
re-applied and logged at INFO, and missing, truncated, or out-of-range files fall back
to defaults instead of failing startup or silently applying bad thresholds.

`backend/tests/test_llm.py` pins the context window the Ollama client requests. Ollama
sizes its compute buffers from the context length, so leaving it to the server default
made generation depend on environment variables set in whatever terminal launched
`ollama serve`.

`backend/tests/test_audit.py` covers the overfit gate — the golden/holdout recall gap —
and the auto-correction contract: on failure the original config is restored and no
reports are returned, because the last candidate's numbers describe a configuration
that is no longer active.

`backend/tests/test_refusal.py` grew from the refusal-text contract into the full
refusal-decision suite. Beyond the original parsing matrix it now pins that evidence
overlap matches whole tokens rather than substrings (`"art"` must not match
*particles*), and that answerable questions are never short-circuited as private or
time-dependent — the bug that made *"Who won World War I?"*, *"What is alternating
current?"* and *"What is ME/CFS?"* hard-refuse before retrieval ever ran.

Frontend component tests cover the visible query flow at component level:
`AnswerView` renders answer text, `CitationList` renders nothing for empty citations
and expands citation detail on click, `QueryBox` handles input submission plus
loading-disabled state, and `QualityPanel` renders the metrics table, runs an audit,
disables the button when a deployment refuses audits at the edge, and reports a
concurrent audit without disabling it.

---

## Known gaps

- No numeric pytest/Vitest coverage report is configured.
- No live service integration test starts FastAPI, Qdrant, Redis, Ollama, and the
  frontend proxy together.
- No deployment smoke test verifies a deployed revision's health, query, refusal, and
  frontend behavior.
- The frontend suite is not wired into CI. `package-lock.json` is now committed and
  the suite runs reproducibly, so this is a workflow gap rather than a dependency one.
- No static type-checking gate on the backend (the frontend has `tsc --noEmit`).
- **The eval suites score the retrieval decision, not what the user receives.**
  Every metric is computed from the evidence gate's verdict, before the model runs, so
  a query the gate accepts on weak evidence may still be refused by the model. The
  numbers bound what the user receives in neither direction.
- **Refusal is the open defect, and it is measured rather than suspected.** The suites
  were rebaselined onto honest expectations, and the gates went red as intended:
  `false_accept_rate` is **0.50 / 0.60 / 0.50** on golden / holdout / adversarial and
  **0.600** on the 24,694-article serving corpus, against a 0.10 gate. It rises with
  corpus size (0.450 at 60 articles, 0.500 at 500, 0.600 at 24,694) because
  `refusal_min_overlap_terms = 1` accepts on a single shared token, so a larger corpus
  offers more chances for an irrelevant chunk to supply it. The fix is evidence
  scoring that weighs *which* terms overlap, not a threshold change; `make eval-audit`
  reports `suspect_overfit` deliberately until then.

---

## How to run

```bash
cd projects/rag-wikipedia
make test                                   # backend pytest, verbose
make lint                                   # backend ruff

cd frontend
npm ci                                      # reproducible install from the lockfile
npm run test -- --run                       # frontend Vitest, single run
npx tsc --noEmit                            # frontend type check
```

No Qdrant, Redis, Ollama or Docker is needed for either suite: the vector-store
contract tests use an in-memory Qdrant, and everything else is mocked. That is what
lets `.github/workflows/backend-ci.yml` run lint and tests with no services attached.

## What to say about your tests in an interview

- **"I test the credibility-critical paths hardest"** — refusal logic, citation extraction, chunking determinism, the query path, eval gates, and the Qdrant contract.
- **"I mock the slow dependencies for unit tests, but I keep one real contract test against an in-memory Qdrant"** — because mocking the store is exactly what let a client API break slip into production once; now it fails in CI instead.
- **"Idempotency is a property test"** — I ingest the same articles twice and assert the vector count doesn't change, which proves the deterministic-ID design.
- **"Validation and failure mapping are tested"** — empty/oversized input → 422; a dead dependency → 503; a refusal is a normal 200.
- **"A test that cannot fail is worse than no test"** — `test_retrieval.py` had a refusal test whose query tripped an earlier short-circuit, so it never reached the branch it named, and it patched `settings` where the code reads runtime config. It passed for two wrong reasons. It now asserts the decision *reason*, not just the outcome.
- **"I check that a new test fails against the old code"** — before keeping any regression test, I revert the fix and confirm the test goes red. That is the only evidence that it tests what its name claims.
- **"I do not overclaim coverage"** — the suite has strong behavioral coverage, but numeric line/branch coverage is not configured yet, and the eval numbers describe retrieval rather than the answer the user finally receives.
