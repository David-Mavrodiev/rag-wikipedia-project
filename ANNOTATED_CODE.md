# RAG over Wikipedia — Fully Annotated Source (every line commented)

> The complete codebase needed to build this project from scratch, with a comment on essentially every line.
> Read it top-to-bottom in this order as you have built the whole system. Comments are in English.
>
> **Build order:** config → interfaces (Embedder/LLM) → indexing (chunking, embeddings, vectorstore) →
> query brain (retrieval, prompt, citations) → HTTP layer (models, api, main) → ingestion pipeline → evaluation → frontend → infra.
>
> **Mental model:** two flows share one index.
> Ingestion (offline): `documents → clean → chunk → embed → store in Qdrant`.
> Query (online): `question → embed → search Qdrant → refuse if weak → prompt → LLM → answer + citations`.

---

## Project structure

```
projects/rag-wikipedia/
├── backend/
│   ├── app/
│   │   ├── core/      config.py, chunking.py, embeddings.py, vectorstore.py,
│   │   │              retrieval.py, prompt.py, citations.py, llm.py
│   │   ├── models/    query.py
│   │   ├── api/        query.py, health.py
│   │   └── main.py
│   ├── pipeline/      sources.py, tasks.py, flow.py
│   ├── eval/          metrics.py, run_eval.py, golden.jsonl
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/          App.tsx, components/{QueryBox,AnswerView,CitationList}.tsx
├── docker-compose.yml
└── Makefile
```

---

# BACKEND

## `backend/app/core/config.py` — every tunable in one typed object

```python
from pydantic_settings import BaseSettings, SettingsConfigDict  # base class that reads fields from env vars, plus its config object


class Settings(BaseSettings):  # a single typed object holding every setting; subclassing BaseSettings enables env-var overrides
    # load a local .env file (UTF-8) if present; ignore unknown keys instead of raising an error
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    qdrant_url: str = "http://localhost:6333"      # address of the Qdrant vector DB (override with env QDRANT_URL)
    ollama_url: str = "http://localhost:11434"     # address of the Ollama LLM server (env OLLAMA_URL)
    embed_model: str = "BAAI/bge-small-en-v1.5"    # HuggingFace id of the embedding model (env EMBED_MODEL)
    llm_model: str = "llama3.2:3b"                 # Ollama model tag used for generation (env LLM_MODEL)
    collection: str = "wikipedia"                  # name of the Qdrant collection to read/write
    top_k: int = 5                                 # how many nearest chunks to retrieve per query
    profile: str = "tiny"                          # ingestion size: "tiny" (~500 articles) or "real" (~25k)
    token_budget: int = 3000                       # max tokens of context packed into the prompt
    refusal_threshold: float = 0.3                 # if the best match scores below this, refuse to answer
    allowed_origins: str = "*"                     # CORS allow-list (comma-separated origins); "*" = allow any


settings = Settings()  # instantiate once at import time; every module imports this shared object
```

## `backend/app/core/embeddings.py` — the Embedder interface + a concrete model

```python
from __future__ import annotations          # allows list[float] type hints on older Python versions

from abc import ABC, abstractmethod          # tools to declare an abstract base class (an interface)


class Embedder(ABC):                         # the interface every embedder must satisfy; other code depends on THIS, not a concrete model
    @abstractmethod                          # subclasses MUST implement this method
    def embed(self, text: str) -> list[float]:   # turn one string into one vector
        raise NotImplementedError            # never actually runs (abstract); a safety net if called directly

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:  # turn many strings into many vectors (faster)
        raise NotImplementedError


class BGEEmbedder(Embedder):                 # one concrete implementation, using a local sentence-transformers model
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer  # lazy import: heavy library, load only when instantiated
        self._model = SentenceTransformer(model_name)          # download/load the model into memory once

    def embed(self, text: str) -> list[float]:
        # encode() returns a numpy array; normalize_embeddings=True scales it to length 1
        # (so cosine similarity == dot product); .tolist() converts to a plain Python list for JSON/Qdrant
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()  # same, but vectorises a whole list at once
```

## `backend/app/core/llm.py` — the LLM interface + Ollama backend

```python
from __future__ import annotations
from abc import ABC, abstractmethod


class LLM(ABC):                              # the interface every LLM backend must satisfy (swappable, like Embedder)
    @abstractmethod
    def generate(self, prompt: str) -> str:  # take a full prompt string, return the model's generated text
        raise NotImplementedError


class OllamaLLM(LLM):                         # concrete backend that talks to a local Ollama server
    def __init__(self, model: str, base_url: str):
        import ollama                         # lazy import of the ollama client library
        self._model = model                   # e.g. "llama3.2:3b"
        self._client = ollama.Client(host=base_url)  # HTTP client pointed at the Ollama server

    def generate(self, prompt: str) -> str:
        response = self._client.generate(model=self._model, prompt=prompt)  # one-shot text completion
        return response["response"]           # the SDK returns a dict; extract the generated text field
```

## `backend/app/core/chunking.py` — token-based splitting with deterministic IDs

```python
from __future__ import annotations
import hashlib                               # to build stable SHA-256 point IDs
from dataclasses import dataclass            # lightweight class to hold a chunk's data
import tiktoken                              # tokenizer library (count/split text by tokens)

CHUNK_SIZE = 512                             # target chunk length, in tokens
CHUNK_OVERLAP = 64                           # tokens shared between consecutive chunks (keeps context across seams)

_enc = tiktoken.get_encoding("cl100k_base") # the tokenizer instance; the SAME one used for the retrieval budget


@dataclass                                   # auto-generates __init__, __repr__, etc.
class Chunk:
    text: str                                # the chunk's text
    source_id: str                           # id of the article this chunk came from
    chunk_index: int                         # position of this chunk within its article (0, 1, 2, …)
    point_id: str                            # deterministic id used as the Qdrant point id


def _make_point_id(source_id: str, chunk_index: int) -> str:
    raw = f"{source_id}::{chunk_index}"                     # unique key: which article + which chunk position
    return hashlib.sha256(raw.encode()).hexdigest()[:32]   # hash it; take the first 32 hex chars as the id


def chunk_text(text: str, source_id: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    tokens = _enc.encode(text)               # convert the whole article into a list of token ids
    chunks: list[Chunk] = []                 # accumulator for the chunks we produce
    start = 0                                # index of the first token of the current window
    idx = 0                                  # running chunk counter (feeds the deterministic id)
    while start < len(tokens):               # keep going until we've covered every token
        end = min(start + size, len(tokens)) # window end (don't run past the last token)
        chunk_tokens = tokens[start:end]     # the slice of tokens for this chunk
        chunk_text_str = _enc.decode(chunk_tokens)   # turn those tokens back into a text string
        point_id = _make_point_id(source_id, idx)    # the stable id for this chunk
        chunks.append(                       # build and store the Chunk object
            Chunk(text=chunk_text_str, source_id=source_id, chunk_index=idx, point_id=point_id)
        )
        if end == len(tokens):               # if this window reached the end, we're done
            break
        start += size - overlap              # advance the window, leaving `overlap` tokens of overlap
        idx += 1                             # next chunk index
    return chunks                            # the full list of chunks for this article
```

## `backend/app/core/vectorstore.py` — the only file that talks to Qdrant

```python
from __future__ import annotations
import logging
from typing import Any
from qdrant_client import QdrantClient                            # the Qdrant Python client
from qdrant_client.models import Distance, PointStruct, VectorParams  # typed helpers for collection config and points

logger = logging.getLogger(__name__)         # module-level logger
EXPECTED_DIM = 384                            # the embedding size we expect (bge-small = 384 dimensions)


class QdrantStore:                            # thin wrapper; the ONLY place in the app that imports qdrant_client
    def __init__(self, url: str, collection: str):
        # connect to Qdrant; timeout=60s because bulk upserts can exceed the client's 5s default under load
        self._client = QdrantClient(url=url, timeout=60)
        self._collection = collection         # remember which collection this store manages

    def ensure_collection(self, dim: int = EXPECTED_DIM) -> None:
        if dim != EXPECTED_DIM:               # guard: the DB's vector size must match the embedder's output size
            raise ValueError(f"Expected embedding dim={EXPECTED_DIM}, got {dim}. Check EMBED_MODEL config.")
        existing = [collection.name for collection in self._client.get_collections().collections]  # existing names
        if self._collection not in existing:  # only create it if it doesn't already exist (idempotent)
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),  # 384-d vectors, cosine distance
            )
            logger.info("Created collection '%s' dim=%s", self._collection, dim)

    def upsert_batch(self, points: list[dict[str, Any]]) -> None:
        structs = [                           # convert our plain dicts into Qdrant PointStruct objects
            PointStruct(id=point["id"], vector=point["vector"], payload=point["payload"])
            for point in points
        ]
        # upsert = insert-or-update; because ids are deterministic, re-running overwrites instead of duplicating
        self._client.upsert(collection_name=self._collection, points=structs)

    def search(self, vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        # query_points = Qdrant's Query API; it replaced the client's removed search()
        results = self._client.query_points(  # nearest-neighbour search by cosine similarity
            collection_name=self._collection, query=vector, limit=top_k, with_payload=True,
        ).points                              # .points unwraps the QueryResponse envelope
        return [                              # normalise Qdrant's results into simple dicts
            {
                "score": result.score,                          # similarity score (higher = closer)
                "text": result.payload.get("text", ""),         # the chunk text
                "title": result.payload.get("title", ""),       # the article title
                "source_id": result.payload.get("source_id", ""),  # the article id
            }
            for result in results
        ]

    def count(self) -> int:
        return self._client.count(collection_name=self._collection).count  # number of points stored
```

> **Note on versions.** The public method is still named `search()` — that is *our*
> interface and `retrieval.py` calls it. What changed is the client call underneath:
> `query_points()` (Query API, client ≥1.10) instead of the removed `search()` (gone
> in client 1.16). **Client and server must agree:** this repo runs client
> `>=1.12,<2` against server `v1.18.3`. A modern client against a 1.9.2 server gives
> `'QdrantClient' object has no attribute 'query_points'` → `503 Vector store
> unavailable`. `tests/test_vectorstore.py` exercises the real client method so this
> mismatch fails a test rather than a demo.

## `backend/app/core/retrieval.py` — search, refuse, fit the token budget

```python
from __future__ import annotations
import logging
import tiktoken
from app.core.config import settings         # shared settings (top_k, token_budget, refusal_threshold)
from app.core.embeddings import Embedder     # the interface type (used only for the type hint)
from app.core.vectorstore import QdrantStore # the vector store type (used only for the type hint)

logger = logging.getLogger(__name__)
_enc = tiktoken.get_encoding("cl100k_base")  # SAME tokenizer as chunking, so token counts agree everywhere


def _token_count(text: str) -> int:
    return len(_enc.encode(text))            # how many tokens a piece of text is


def retrieve(query: str, embedder: Embedder, store: QdrantStore,
             top_k: int | None = None, token_budget: int | None = None) -> tuple[list[dict], bool]:
    # returns (chunks, is_empty); is_empty=True is the "refuse" signal
    if top_k is None:                        # fall back to the config default if not passed
        top_k = settings.top_k
    if token_budget is None:
        token_budget = settings.token_budget

    vector = embedder.embed(query)           # embed the question (must be the same model used at ingestion)
    results = store.search(vector, top_k=top_k)  # get the top_k nearest chunks

    if not results:                          # refusal case (a): nothing found at all
        return [], True
    if results[0]["score"] < settings.refusal_threshold:  # refusal case (b): even the best hit is too weak
        return results, True

    kept: list[dict] = []                    # chunks we'll actually pass to the LLM
    used = 0                                 # running token total of `kept`
    for index, result in enumerate(results): # walk hits from best to worst
        tokens = _token_count(result["text"])# size of this chunk in tokens
        if index == 0:                       # ALWAYS keep the single best chunk…
            kept.append(result)
            used = tokens
            continue
        if used + tokens > token_budget:     # …then stop once adding more would blow the budget
            break
        kept.append(result)                  # otherwise keep it
        used += tokens                       # and add its tokens to the running total

    return kept, len(kept) == 0              # is_empty is True only if we somehow kept nothing
```

## `backend/app/core/prompt.py` — force grounding, require citations, offer a refusal string

```python
from __future__ import annotations

# system instruction prepended to every prompt: (1) answer ONLY from context (grounding),
# (2) cite inline with [n], (3) emit an exact refusal string when the context is useless
SYSTEM_PROMPT = """\
You are a helpful assistant that answers questions based only on the provided context.
Each context chunk is labeled with a citation number [1], [2], etc.
You MUST cite your sources inline using [number] format.
If the context does not contain relevant information, respond with exactly:
"I don't know based on the provided context."
"""


def build_prompt(query: str, chunks: list[dict]) -> str:
    context_parts = []                       # will hold one formatted block per chunk
    for index, chunk in enumerate(chunks, 1):# number chunks starting at 1 → [1], [2], … (the citation contract)
        context_parts.append(f"[{index}] (Source: {chunk['title']})\n{chunk['text']}")  # label + text
    context = "\n\n".join(context_parts)     # join the blocks with blank lines
    return f"""{SYSTEM_PROMPT}

Context:
{context}

Question: {query}

Answer:"""                                    # final prompt = rules + context + question


def build_refusal_prompt(query: str) -> str: # variant used in tests to force the no-context path
    return f"""{SYSTEM_PROMPT}

Context: (none)

Question: {query}

Answer:"""
```

## `backend/app/core/citations.py` — only count what the model actually cited

```python
from __future__ import annotations
import re                                    # regular expressions, to find [n] markers in the answer


def extract_citation_indices(answer: str) -> list[int]:
    matches = re.findall(r"\[(\d+)\]", answer)          # find every [number]; capture the digits inside
    return sorted({int(match) for match in matches})    # dedupe (set) then sort; [1],[2],[1] → [1, 2]


def build_citations(chunks: list[dict], cited_indices: list[int]) -> list[dict]:
    result = []                              # the citations we'll return
    for idx in cited_indices:                # for each number the model actually cited
        chunk_index = idx - 1                # [1] refers to chunks[0], so shift by one
        if 0 <= chunk_index < len(chunks):   # ignore out-of-range / hallucinated numbers (e.g. [9] of 5)
            chunk = chunks[chunk_index]      # the chunk that citation points to
            text = chunk["text"]
            result.append(
                {
                    "index": idx,                                # the citation number as shown
                    "title": chunk.get("title", ""),            # source article title
                    "source_id": chunk.get("source_id", ""),    # source article id
                    "excerpt": text[:200] + "..." if len(text) > 200 else text,  # short preview (<=200 chars)
                }
            )
    return result
```

## `backend/app/models/query.py` — the request/response contract

```python
from __future__ import annotations
from pydantic import BaseModel, Field        # BaseModel = validated data class; Field = per-field rules


class QueryRequest(BaseModel):               # shape of the incoming POST body
    # question is required; must be 1–500 chars. A violation becomes an automatic HTTP 422 before our code runs
    question: str = Field(..., min_length=1, max_length=500)


class Citation(BaseModel):                   # shape of one citation in the response
    index: int                               # the [n] number
    title: str                               # source article title
    source_id: str                           # source article id
    excerpt: str                             # short text preview


class QueryResponse(BaseModel):              # shape of the outgoing response
    answer: str                              # the generated (or refusal) text
    citations: list[Citation]                # zero or more citations
    refused: bool = False                    # EXPLICIT refusal state — see refusal.py
```

## `backend/app/core/refusal.py` — the refusal contract

```python
from __future__ import annotations

# Single source of truth for what "refused" means. Shared by the API (returns it
# verbatim when retrieval is empty/low-score), the prompt (tells the model to emit
# it) and any client that must tell a refusal from a real answer. One definition
# here stops those three from drifting apart.
REFUSAL_MESSAGE = "I don't know based on the provided context."

# Smart (curly) quotes -> ASCII. Model output is not byte-stable: the same refusal
# can come back curly-quoted, and handling only the ASCII forms is exactly how a
# refusal silently gets reported as a grounded answer.
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def is_refusal(answer: str) -> bool:         # decided by the TEXT only
    # Deliberately does NOT look at citations. A grounded answer that omits [n]
    # markers (small local models do this intermittently) is a real answer, not a
    # refusal — conflating "no citations" with "refused" is the bug this prevents.
    normalized = answer.strip().translate(_SMART_QUOTES).strip('"').strip().lower()
    return normalized.startswith(REFUSAL_MESSAGE.lower())
```

Two refusal kinds share this contract: a **hard refusal** (retrieval empty or below
`refusal_threshold` — the API returns `REFUSAL_MESSAGE` and never calls the LLM) and
a **soft refusal** (the model itself declines despite having context). Both set
`refused: true`, so a client never has to guess.

## `backend/app/api/health.py` — liveness probe

```python
from fastapi import APIRouter
router = APIRouter()                          # a group of routes, mounted by main.py


@router.get("/health")                        # GET /health
async def health() -> dict:
    return {"status": "ok"}                   # liveness only: "process is up"; does NOT probe Qdrant/Ollama
```

## `backend/app/api/query.py` — the orchestrator (the whole system in ~20 lines)

```python
from __future__ import annotations
import logging
from functools import lru_cache              # memoisation decorator, used here to build lazy singletons
from fastapi import APIRouter, HTTPException
from app.core.citations import build_citations, extract_citation_indices
from app.core.config import settings
from app.core.embeddings import BGEEmbedder
from app.core.llm import OllamaLLM
from app.core.prompt import build_prompt
from app.core.refusal import REFUSAL_MESSAGE, is_refusal
from app.core.retrieval import retrieve
from app.core.vectorstore import QdrantStore
from app.models.query import Citation, QueryRequest, QueryResponse

logger = logging.getLogger(__name__)
router = APIRouter()                          # groups this file's routes; included by main.py
# REFUSAL_MESSAGE / is_refusal are imported from app.core.refusal (see above) —
# the refusal string is defined once, never re-declared here.


@lru_cache(maxsize=1)                         # build once, reuse forever (a lazy singleton)
def _embedder() -> BGEEmbedder:
    return BGEEmbedder(model_name=settings.embed_model)   # loads the embedding model on first call only


@lru_cache(maxsize=1)
def _store() -> QdrantStore:
    return QdrantStore(url=settings.qdrant_url, collection=settings.collection)  # one shared Qdrant client


@lru_cache(maxsize=1)
def _llm() -> OllamaLLM:
    return OllamaLLM(model=settings.llm_model, base_url=settings.ollama_url)     # one shared Ollama client


@router.post("/query", response_model=QueryResponse)   # POST /query; the response is validated against QueryResponse
async def query(request: QueryRequest) -> QueryResponse:   # request is already validated (1–500 chars)
    try:
        chunks, is_empty = retrieve(request.question, _embedder(), _store())  # embed + search + refusal check
    except Exception as exc:                  # any failure here means the vector store is unreachable
        logger.error("Retrieval error: %s", exc)
        raise HTTPException(status_code=503, detail="Vector store unavailable") from exc

    if is_empty:                              # HARD refusal: skip the (expensive) LLM entirely
        return QueryResponse(answer=REFUSAL_MESSAGE, citations=[], refused=True)

    prompt = build_prompt(request.question, chunks)  # assemble the grounded prompt with [n] markers

    try:
        answer = _llm().generate(prompt)      # call the LLM
    except Exception as exc:                  # any failure here means the LLM is unreachable
        logger.error("LLM error: %s", exc)
        raise HTTPException(status_code=503, detail="LLM unavailable") from exc

    # SOFT refusal: the model declined even though context was available. Decided
    # by the answer TEXT, never by citation count — a grounded answer may have 0.
    if is_refusal(answer):
        return QueryResponse(answer=answer, citations=[], refused=True)

    cited_indices = extract_citation_indices(answer)   # which [n] did the model actually use?
    citations = build_citations(chunks, cited_indices) # map those numbers to real sources + excerpts

    return QueryResponse(                     # the validated response object
        answer=answer,
        citations=[Citation(**citation) for citation in citations],  # dicts → Citation models
        refused=False,                        # answered (possibly with zero citations)
    )
```

## `backend/app/main.py` — assemble the FastAPI app

```python
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # lets browsers on other origins call this API
from app.api.health import router as health_router
from app.api.query import router as query_router
from app.core.config import settings

# configure root logging once: level + a readable format (timestamp / level / logger name / message)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="RAG Wikipedia API", version="0.1.0")  # the ASGI application object uvicorn will serve
app.add_middleware(
    CORSMiddleware,
    # split the comma-separated allow-list from config into a list of origins ("*" = allow any)
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",")],
    allow_methods=["*"],                      # allow any HTTP method
    allow_headers=["*"],                      # allow any request header
)
app.include_router(health_router)             # mount GET /health
app.include_router(query_router)              # mount POST /query
```

---

# INGESTION PIPELINE

## `backend/pipeline/sources.py` — stream articles from Hugging Face

```python
from __future__ import annotations
import logging
from collections.abc import Iterator         # type hint: this function yields items lazily
logger = logging.getLogger(__name__)

# per-profile settings: how many articles to pull. "tiny" for fast dev, "real" for the full pass
PROFILE_CONFIG = {
    "tiny": {"split": "train", "streaming": True, "n_articles": 500},
    "real": {"split": "train", "streaming": True, "n_articles": 25_000},
}


def iter_articles(profile: str = "tiny") -> Iterator[dict]:  # yields dicts with keys id/title/text
    """Yield dicts with keys: id, title, text."""
    from datasets import load_dataset         # lazy import of the HuggingFace datasets library
    cfg = PROFILE_CONFIG.get(profile, PROFILE_CONFIG["tiny"])  # pick the profile (default to tiny)
    n_articles = cfg["n_articles"]            # how many articles to take
    logger.info("Loading %s articles from wikimedia/wikipedia (%s)", n_articles, profile)
    dataset = load_dataset(                   # open the dataset as a stream (nothing is downloaded up front)
        "wikimedia/wikipedia",
        "20231101.en",                        # the English Wikipedia snapshot from 1 Nov 2023
        split=cfg["split"],
        streaming=True,
        trust_remote_code=True,
    )
    for index, row in enumerate(dataset):     # iterate the stream, counting as we go
        if index >= n_articles:               # stop once we've taken enough
            break
        yield {"id": row["id"], "title": row["title"], "text": row["text"]}  # emit a clean article dict
```

## `backend/pipeline/tasks.py` — the per-article steps (Prefect tasks)

```python
from __future__ import annotations
import logging
import re
from typing import Any
from app.core.chunking import Chunk, chunk_text
from prefect import task                     # decorator that turns a function into a Prefect task
from prefect.cache_policies import NO_CACHE  # disables Prefect's result caching (required below)
logger = logging.getLogger(__name__)


def clean_text(text: str) -> str:            # reduce Wikipedia markup to plain prose
    text = re.sub(r"={2,}[^=]+=+", " ", text)                  # remove == Section headings ==
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)  # [[link|label]] → label
    text = re.sub(r"\{\{[^}]*\}\}", "", text)                  # drop {{templates}}
    text = re.sub(r"<[^>]+>", "", text)                        # drop <html tags>
    text = re.sub(r"\s{2,}", " ", text)                        # collapse runs of whitespace
    return text.strip()                                        # trim the ends


@task(name="clean-article")                  # a Prefect task (gains retries / observability / logging)
def clean_article(article: dict) -> dict:
    return {**article, "text": clean_text(article["text"])}    # copy the dict but with cleaned text


@task(name="chunk-article")
def chunk_article(article: dict) -> list[Chunk]:
    return chunk_text(article["text"], source_id=article["id"])  # split the cleaned text into chunks


@task(name="embed-chunks", cache_policy=NO_CACHE)  # NO_CACHE: don't hash the embedder arg (holds a lock → unpicklable)
def embed_chunks(chunks: list[Chunk], embedder: Any) -> list[tuple[Chunk, list[float]]]:
    texts = [chunk.text for chunk in chunks]         # pull just the text out of each chunk
    vectors = embedder.embed_batch(texts)            # embed them all in one batch call
    return list(zip(chunks, vectors))                # pair each chunk with its vector


@task(name="upsert-to-qdrant", cache_policy=NO_CACHE)  # NO_CACHE: the store arg holds a thread lock (unpicklable)
def upsert_to_qdrant(chunk_vectors: list[tuple[Chunk, list[float]]], vectorstore: Any, article: dict) -> int:
    points = []                              # build the list of points to write
    for chunk, vector in chunk_vectors:
        points.append(
            {
                "id": chunk.point_id,        # deterministic id → idempotent writes (no duplicates on re-run)
                "vector": vector,            # the embedding
                "payload": {                 # metadata stored alongside the vector (returned by search)
                    "text": chunk.text,
                    "source_id": chunk.source_id,
                    "chunk_index": chunk.chunk_index,
                    "title": article["title"],
                },
            }
        )
    vectorstore.upsert_batch(points)         # write them all to Qdrant
    return len(points)                       # how many points we wrote (for totals/logging)
```

## `backend/pipeline/flow.py` — the ingestion conductor

```python
from __future__ import annotations
import logging
import sys
from pathlib import Path
from prefect import flow                     # decorator that turns a function into a Prefect flow (orchestrates tasks)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # add backend/ to the import path so `app`/`pipeline` import when run directly

logger = logging.getLogger(__name__)


@flow(name="wikipedia-ingest")               # the top-level flow that runs the whole ingestion
def ingest_flow(profile: str | None = None):
    from app.core.config import settings                     # imported inside so the path insert above is in effect
    from app.core.embeddings import BGEEmbedder
    from app.core.vectorstore import QdrantStore
    from pipeline.sources import iter_articles
    from pipeline.tasks import chunk_article, clean_article, embed_chunks, upsert_to_qdrant

    profile = profile or settings.profile                    # use the given profile, else the config default
    embedder = BGEEmbedder(model_name=settings.embed_model)  # load the embedding model once for the whole run
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)  # one Qdrant client for the run
    store.ensure_collection(dim=384)                         # create the collection if it doesn't exist yet

    total = 0                                # running count of chunks written
    for article in iter_articles(profile):   # stream articles one at a time
        cleaned = clean_article(article)     # step 1: strip markup
        if len(cleaned["text"]) < 100:       # skip near-empty stubs (not worth indexing)
            continue
        chunks = chunk_article(cleaned)      # step 2: split into token chunks
        if not chunks:                       # skip if it produced no chunks
            continue
        chunk_vectors = embed_chunks(chunks, embedder)       # step 3: embed the chunks
        inserted = upsert_to_qdrant(chunk_vectors, store, cleaned)  # step 4: write to Qdrant
        total += inserted                    # accumulate the count

    logger.info("Ingestion complete: %s chunks upserted", total)
    return total                             # total chunks written


if __name__ == "__main__":                   # allow `python backend/pipeline/flow.py` to run the flow
    ingest_flow()
```

---

# EVALUATION

## `backend/eval/metrics.py` — pure, unit-testable metric functions

```python
from __future__ import annotations


def recall_at_k(retrieved_texts: list[str], expected_keywords: list[str], k: int) -> float:
    """Fraction of expected keywords found in top-k retrieved texts."""
    top_k = retrieved_texts[:k]              # keep only the first k retrieved chunk texts
    combined = " ".join(top_k).lower()       # join them into one lowercase blob
    hits = sum(1 for keyword in expected_keywords if keyword.lower() in combined)  # count keywords present
    return hits / len(expected_keywords) if expected_keywords else 0.0  # fraction found (0 if no keywords given)


def reciprocal_rank(retrieved_texts: list[str], expected_keywords: list[str]) -> float:
    """Reciprocal rank of the first chunk containing any expected keyword."""
    for index, text in enumerate(retrieved_texts, 1):  # walk chunks from rank 1 downward
        if any(keyword.lower() in text.lower() for keyword in expected_keywords):  # first relevant chunk?
            return 1.0 / index               # reciprocal of its rank (rank 1 → 1.0, rank 2 → 0.5, …)
    return 0.0                               # none relevant → 0


def mrr(scores: list[float]) -> float:
    """Mean reciprocal rank over a list of reciprocal-rank scores."""
    if not scores:                           # empty list → 0 (avoid divide-by-zero)
        return 0.0
    return sum(scores) / len(scores)         # average of the per-question reciprocal ranks


def refusal_accuracy(refused_flags: list[bool]) -> float:
    """Fraction of unanswerable questions that were correctly refused."""
    if not refused_flags:
        return 0.0
    return sum(refused_flags) / len(refused_flags)  # True counts as 1 → fraction refused


def groundedness(answer: str, context_chunks: list[str]) -> float:
    """Simple lexical groundedness: fraction of answer tokens found in the context."""
    if not context_chunks or not answer.strip():  # nothing to compare → 0
        return 0.0
    answer_tokens = set(answer.lower().split())        # unique words in the answer
    context_text = " ".join(context_chunks).lower()
    context_tokens = set(context_text.split())         # unique words in the context
    overlap = answer_tokens & context_tokens           # words that appear in both
    return len(overlap) / len(answer_tokens) if answer_tokens else 0.0  # fraction of answer supported by context
```

## `backend/eval/run_eval.py` — run metrics, write a report, gate on 0.8

```python
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))  # add backend/ to the import path

RECALL_GATE = 0.8                            # minimum acceptable recall@k
REFUSAL_GATE = 0.8                           # minimum acceptable refusal accuracy


def main() -> None:
    from app.core.config import settings                  # imported inside main so the path insert is in effect
    from app.core.embeddings import BGEEmbedder
    from app.core.retrieval import retrieve
    from app.core.vectorstore import QdrantStore
    from eval.metrics import mrr, recall_at_k, reciprocal_rank, refusal_accuracy

    golden_path = Path(__file__).parent / "golden.jsonl"  # the golden question set lives next to this file
    golden = [json.loads(line) for line in golden_path.read_text().splitlines() if line.strip()]  # parse each JSON line

    if not golden:                           # nothing to evaluate → warn and stop
        logger.warning("golden.jsonl is empty — nothing to evaluate.")
        return

    answerable = [item for item in golden if not item.get("expected_refusal")]   # questions that have an answer
    unanswerable = [item for item in golden if item.get("expected_refusal")]     # questions that should be refused

    embedder = BGEEmbedder(model_name=settings.embed_model)  # load the embedder (eval uses retrieval only, no LLM)
    store = QdrantStore(url=settings.qdrant_url, collection=settings.collection)

    k = settings.top_k                       # retrieval depth used for the metrics
    recall_scores: list[float] = []          # per-question recall@k
    reciprocal_rank_scores: list[float] = [] # per-question reciprocal rank

    for item in answerable:                  # evaluate retrieval on answerable questions
        question = item["question"]
        expected = item["expected_sources"]  # keywords expected to appear in the right chunks
        chunks, _ = retrieve(question, embedder, store, top_k=k)  # retrieve (ignore the refusal flag here)
        texts = [chunk["text"] for chunk in chunks]              # just the chunk texts
        recall_score = recall_at_k(texts, expected, k=k)         # how many expected keywords were retrieved
        rr_score = reciprocal_rank(texts, expected)              # how high the first relevant chunk was
        recall_scores.append(recall_score)
        reciprocal_rank_scores.append(rr_score)
        logger.info("Q: %r | recall@%s=%.2f | RR=%.2f", question[:50], k, recall_score, rr_score)

    refused_flags: list[bool] = []           # per-question "did we refuse?" for unanswerable questions
    for item in unanswerable:
        question = item["question"]
        _, refused = retrieve(question, embedder, store, top_k=k)  # only the refusal flag matters here
        refused_flags.append(refused)
        logger.info("Q: %r | refused=%s", question[:50], refused)

    report = {                               # assemble the metrics report
        f"recall@{k}": sum(recall_scores) / len(answerable) if answerable else 0.0,
        "mrr": mrr(reciprocal_rank_scores),
        "refusal_accuracy": refusal_accuracy(refused_flags),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
    }

    print("\n=== Evaluation Report ===")     # print the report to the console
    for key, value in report.items():
        print(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")

    report_path = Path(__file__).parent / "report.json"  # also write it to report.json (the demo console reads this)
    report_path.write_text(json.dumps(report, indent=2))
    logger.info("Report written to %s", report_path)

    failures = []                            # collect any gate that failed
    if answerable and report[f"recall@{k}"] < RECALL_GATE:
        failures.append(f"recall@{k}={report[f'recall@{k}']:.2f} < {RECALL_GATE}")
    if unanswerable and report["refusal_accuracy"] < REFUSAL_GATE:
        failures.append(f"refusal_accuracy={report['refusal_accuracy']:.2f} < {REFUSAL_GATE}")

    if failures:                             # if any gate failed, print and exit non-zero (fails CI)
        print(f"\nGATE FAILED: {'; '.join(failures)}")
        sys.exit(1)
    print("\nGATE PASSED")                   # otherwise the run passes


if __name__ == "__main__":
    main()
```

## `backend/eval/golden.jsonl` — the evaluation set (data, not code)

20 questions: 15 answerable (`expected_sources` = keywords that should appear in retrieved chunks) + 5 unanswerable (`expected_refusal: true`). One JSON object per line:

```jsonl
{"question": "Who created Python?", "expected_sources": ["Guido van Rossum"]}
{"question": "What is machine learning?", "expected_sources": ["machine learning", "artificial intelligence"]}
{"question": "When was Wikipedia founded?", "expected_sources": ["Wikipedia", "2001"]}
{"question": "What is the speed of light?", "expected_sources": ["light", "299"]}
{"question": "Who wrote Hamlet?", "expected_sources": ["Shakespeare", "William"]}
{"question": "What is DNA?", "expected_sources": ["deoxyribonucleic acid", "DNA"]}
{"question": "What country is Paris in?", "expected_sources": ["France", "Paris"]}
{"question": "What is the capital of Japan?", "expected_sources": ["Tokyo", "Japan"]}
{"question": "What is photosynthesis?", "expected_sources": ["photosynthesis", "plant", "chlorophyll"]}
{"question": "Who invented the telephone?", "expected_sources": ["Alexander Graham Bell", "telephone"]}
{"question": "What is the Pythagorean theorem?", "expected_sources": ["Pythagorean", "triangle", "hypotenuse"]}
{"question": "What is the largest planet?", "expected_sources": ["Jupiter", "planet"]}
{"question": "What is gravity?", "expected_sources": ["gravity", "Newton", "force"]}
{"question": "Who painted the Mona Lisa?", "expected_sources": ["Leonardo da Vinci", "Mona Lisa"]}
{"question": "What is the human genome?", "expected_sources": ["genome", "DNA", "genes"]}
{"question": "What did I have for breakfast this morning?", "expected_sources": [], "expected_refusal": true}
{"question": "What is the name of my neighbor's cat?", "expected_sources": [], "expected_refusal": true}
{"question": "How many unread emails are in my inbox right now?", "expected_sources": [], "expected_refusal": true}
{"question": "What is the Wi-Fi password at my local coffee shop?", "expected_sources": [], "expected_refusal": true}
{"question": "Where did I leave my car keys yesterday?", "expected_sources": [], "expected_refusal": true}
```

---

# FRONTEND (React + Vite + TypeScript)

## `frontend/src/App.tsx` — top-level component and API call

```tsx
import { useState } from 'react'                    // React hook for local component state
import AnswerView from './components/AnswerView'    // child components
import CitationList from './components/CitationList'
import QueryBox from './components/QueryBox'

export interface Citation {                          // the shape of one citation (mirrors the backend model)
  index: number
  title: string
  source_id: string
  excerpt: string
}

export interface QueryResult {                       // the shape of the /query response
  answer: string
  citations: Citation[]
}

// API base URL: empty string in local dev (nginx proxies same-origin); Azure bakes the real URL at build time
const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export default function App() {
  const [result, setResult] = useState<QueryResult | null>(null)  // the last answer (or null)
  const [loading, setLoading] = useState(false)                    // whether a request is in flight
  const [error, setError] = useState<string | null>(null)          // the last error message (or null)

  const handleQuery = async (question: string) => {  // called when the user submits a question
    setLoading(true)                                 // show the loading state
    setError(null)                                   // clear any previous error
    setResult(null)                                  // clear any previous answer
    try {
      const response = await fetch(`${API_BASE}/query`, {   // POST the question to the API
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      if (!response.ok) {                            // non-2xx (e.g. 503) → throw so the catch handles it
        throw new Error(`HTTP ${response.status}`)
      }
      const data: QueryResult = await response.json() // parse the JSON body
      setResult(data)                                // store the answer + citations
    } catch (error) {                                // network error or thrown HTTP error
      setError(error instanceof Error ? error.message : 'Unknown error')
    } finally {
      setLoading(false)                              // always clear the loading state
    }
  }

  return (
    <div className="app">
      <h1>RAG Wikipedia</h1>
      <QueryBox onSubmit={handleQuery} loading={loading} />   {/* the input + button */}
      {error && <p className="error">{error}</p>}             {/* show an error if present */}
      {result && (                                            /* show the answer + sources if we have a result */
        <>
          <AnswerView answer={result.answer} />
          <CitationList citations={result.citations} />
        </>
      )}
    </div>
  )
}
```

## `frontend/src/components/QueryBox.tsx` — the input + submit

```tsx
import { useState } from 'react'

interface Props {                                    // props this component accepts
  onSubmit: (question: string) => void               // callback fired with the typed question
  loading: boolean                                    // disables the form while a request is in flight
}

export default function QueryBox({ onSubmit, loading }: Props) {
  const [value, setValue] = useState('')             // the current text in the input box

  const handleSubmit = (event: React.FormEvent) => {  // form submit handler
    event.preventDefault()                            // stop the browser from reloading the page
    if (value.trim()) {                               // ignore empty/whitespace-only input
      onSubmit(value.trim())                          // send the trimmed question up to the parent
    }
  }

  return (
    <form onSubmit={handleSubmit} data-testid="query-form">   {/* data-testid attributes are used by the tests */}
      <input
        type="text"
        value={value}                                          // controlled input: React owns the value
        onChange={(event) => setValue(event.target.value)}     // update state on every keystroke
        placeholder="Ask a question about Wikipedia..."
        disabled={loading}                                     // greyed out while loading
        data-testid="query-input"
        style={{ width: '70%', padding: '0.5rem' }}
      />
      <button type="submit" disabled={loading || !value.trim()} data-testid="query-submit">
        {loading ? 'Loading…' : 'Ask'}                         {/* label reflects the loading state */}
      </button>
    </form>
  )
}
```

## `frontend/src/components/AnswerView.tsx` — render the answer

```tsx
interface Props {                                    // props: just the answer string
  answer: string
}

export default function AnswerView({ answer }: Props) {
  return (
    <div
      data-testid="answer-view"
      style={{ margin: '1rem 0', padding: '1rem', background: '#f5f5f5' }}
    >
      <h3>Answer</h3>
      <p>{answer}</p>                                 {/* the generated (or refusal) text */}
    </div>
  )
}
```

## `frontend/src/components/CitationList.tsx` — expandable sources

```tsx
import { useState } from 'react'
import type { Citation } from '../App'               // reuse the Citation type from App.tsx

interface Props {                                    // props: the list of citations to show
  citations: Citation[]
}

export default function CitationList({ citations }: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())  // which citation indices are open

  if (citations.length === 0) {                      // nothing to show (e.g. a refusal) → render nothing
    return null
  }

  const toggle = (index: number) => {                // open/close one citation's excerpt
    setExpanded((previous) => {
      const next = new Set(previous)                 // copy the set (don't mutate state directly)
      if (next.has(index)) {
        next.delete(index)                           // it was open → close it
      } else {
        next.add(index)                              // it was closed → open it
      }
      return next
    })
  }

  return (
    <div data-testid="citation-list">
      <h3>Sources</h3>
      {citations.map((citation) => (                 // render one block per citation
        <div
          key={citation.index}                       // React needs a stable key per list item
          style={{ marginBottom: '0.5rem', border: '1px solid #ddd', padding: '0.5rem' }}
        >
          <button onClick={() => toggle(citation.index)} data-testid={`citation-toggle-${citation.index}`}>
            [{citation.index}] {citation.title}       {/* click to expand/collapse */}
          </button>
          {expanded.has(citation.index) && (          /* show the excerpt only when expanded */
            <p
              data-testid={`citation-excerpt-${citation.index}`}
              style={{ marginTop: '0.5rem', fontSize: '0.9em' }}
            >
              {citation.excerpt}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}
```

---

# INFRASTRUCTURE

## `backend/pyproject.toml` — dependencies (note the Qdrant pin)

```toml
[project]
name = "rag-wikipedia-backend"
version = "0.1.0"
requires-python = ">=3.12"                     # Python 3.12+
dependencies = [
    "fastapi>=0.111",                          # the web framework
    "uvicorn[standard]>=0.30",                 # the ASGI server that runs FastAPI
    "pydantic-settings>=2.3",                  # env-driven Settings
    "qdrant-client>=1.12,<2",                  # needs query_points (Query API); matches the v1.18.3 server
    "sentence-transformers>=3.0",              # the embedding model runtime
    "prefect>=3,<4",                           # ingestion orchestration (pin the major: 2.x vs 3.x differ)
    "datasets>=2.19",                          # HuggingFace datasets (the Wikipedia corpus)
    "tiktoken>=0.7",                           # tokenizer for chunking + the context budget
    "httpx>=0.27",                             # HTTP client (used by tests/clients)
    "ollama>=0.2",                             # the Ollama client
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.4", "httpx>=0.27"]  # test + lint tooling

[build-system]
requires = ["hatchling"]                       # build backend
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app", "pipeline", "eval"]         # the importable packages

[tool.hatch.build.targets.sdist]
include = ["app", "pipeline", "eval", "tests"]

[tool.pytest.ini_options]
asyncio_mode = "auto"                          # let pytest run async tests without extra decorators
testpaths = ["tests"]
```

## `docker-compose.yml` — the four services

```yaml
services:
  qdrant:
    image: qdrant/qdrant:v1.18.3                # vector DB; Query API needs server >=1.10. Do NOT start
                                                # this over a v1.9.2 volume — it panics (exit 101). Fresh
                                                # volume + re-ingest.
    ports: ["6333:6333"]                        # expose the REST/gRPC port to the host
    volumes: [qdrant_data:/qdrant/storage]      # persist vectors across restarts
    healthcheck:
      # the image ships no wget/curl; use a bash TCP probe to check the port is open
      test: ["CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/6333' || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5

  ollama:
    image: ollama/ollama:latest                 # the self-hosted LLM server
    ports: ["11434:11434"]
    volumes: [ollama_data:/root/.ollama]        # persist pulled model weights (~2 GB) across restarts
    healthcheck:
      test: ["CMD", "ollama", "list"]           # the CLI itself is the health probe (no wget/curl in the image)
      interval: 15s
      timeout: 10s
      retries: 10

  api:
    build: { context: ./backend, dockerfile: Dockerfile }  # build the FastAPI image from ./backend
    ports: ["8000:8000"]
    environment:                                # point the API at the other containers by service name
      - QDRANT_URL=http://qdrant:6333
      - OLLAMA_URL=http://ollama:11434
      - PROFILE=${PROFILE:-tiny}
    volumes: [hf_cache:/root/.cache/huggingface]  # cache the embedding model across restarts
    depends_on:                                 # wait for the deps to be healthy before starting
      qdrant: { condition: service_healthy }
      ollama: { condition: service_healthy }
    healthcheck:
      # python:slim has no wget/curl; probe /health with the stdlib urllib
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 5

  frontend:
    build: { context: ./frontend, dockerfile: Dockerfile }
    ports: ["5173:80"]                          # nginx serves the built React app on 5173 → 80
    depends_on:
      api: { condition: service_healthy }

volumes: { qdrant_data: , ollama_data: , hf_cache: }   # named volumes for persistence
```

## `backend/Dockerfile`

```dockerfile
FROM python:3.12-slim                           # small Python base image
WORKDIR /app                                    # working directory inside the container
RUN pip install uv                              # install uv (fast installer)
ENV UV_TORCH_BACKEND=cpu                        # install CPU-only torch (no GPU in the container; CUDA wheels are huge)
COPY pyproject.toml .                           # copy the manifest first…
RUN uv pip install --system -r pyproject.toml --extra dev   # …so this dependency layer is cached across source edits
COPY . .                                        # then copy the source
RUN uv pip install --system --no-deps -e .      # install the local package (no deps: already installed above)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]   # run the API
```

## `Makefile`

```makefile
.PHONY: up down pull-model ingest eval test lint   # these targets aren't files
up:                                               # build + start all services in the background
	docker compose up --build -d
pull-model:                                       # one-time: download the LLM weights into the ollama volume
	docker compose exec ollama ollama pull llama3.2:3b
down:                                             # stop and remove the containers
	docker compose down
ingest:                                           # run the ingestion flow (PROFILE=tiny|real)
	PROFILE=$(PROFILE) uv run python backend/pipeline/flow.py
eval:                                             # run the eval harness (writes report.json, gates on 0.8)
	uv run python backend/eval/run_eval.py
test:                                             # run the backend tests
	cd backend && uv run pytest tests/ -v
lint:                                             # run the linter
	cd backend && uv run ruff check app/ pipeline/ eval/ tests/
```

---

# HOW TO RUN IT (from scratch)

```bash
# 0) prerequisites: Docker Desktop, Python 3.12 + uv, Node 20 (frontend only)
# Run from projects/rag-wikipedia (its pyproject.toml declares a uv WORKSPACE whose
# only member is backend/, so the shared .venv is created here and covers the backend).
cd projects/rag-wikipedia
uv sync --all-extras --python 3.12          # create .venv and install everything from backend/pyproject.toml

# 1) start the four services
make up

# 2) one-time: pull the LLM weights (~2 GB, persists in the ollama volume)
make pull-model

# 3) ingest the tiny corpus (~500 articles → ~6k vectors); re-running is a no-op (idempotent)
make ingest PROFILE=tiny

# 4) ask a question
curl -X POST localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question":"What is machine learning?"}'

# 5) prove quality: recall@k, MRR, refusal accuracy + a 0.8 gate
make eval

# UI: http://localhost:5173  (React app)   |   tests: make test
```

---

# OPTIONAL — `backend/app/core/providers.py` (dormant LLM registry)

A **dormant** (fully commented) module that makes the model a one-value config choice. It's inert until you uncomment it, so it never affects the running app — but when activated it lets the same RAG run on **Ollama, Azure OpenAI, OpenAI, or any OpenAI-compatible server** (vLLM, Mistral, Together, Groq…), swapped by a single env var. Because everything depends only on the `Embedder`/`LLM` interfaces, no other file changes.

```python
# name -> factory registry; swap = change LLM_CHOICE / EMBED_CHOICE; add a model = one line
LLM_REGISTRY = {
    "ollama-3b": lambda: OllamaLLM("llama3.2:3b", OLLAMA_URL),   # local, default
    "ollama-1b": lambda: OllamaLLM("llama3.2:1b", OLLAMA_URL),   # smaller local
    "azure":     lambda: AzureOpenAILLM(AZURE_CHAT_DEPLOYMENT, AZURE_ENDPOINT, AZURE_KEY),
    "openai":    lambda: OpenAILLM("gpt-4o-mini", OPENAI_KEY, OPENAI_BASE_URL),  # base_url → any OpenAI-compatible API
}
def make_llm(): return LLM_REGISTRY[os.getenv("LLM_CHOICE", "ollama-3b")]()   # one value picks the model
```

Full commented source is in the file itself; activation steps (wiring, deps, the 384→1536 re-ingest caveat) are in **`PROJECT_BLUEPRINT.md` §17**. This is what lets you point the project at a client's LLM on day one at a new job.

---

# HOW IT ALL CONNECTS (recap)

**Ingestion** (`make ingest` → `flow.py`): for each streamed article → `clean_article` → `chunk_article` (deterministic IDs) → `embed_chunks` → `upsert_to_qdrant`. Re-run safely; same IDs overwrite.

**A query** (`POST /query` → `api/query.py`):
1. Pydantic validates the question (1–500 chars, else 422).
2. `retrieve()` embeds it, searches Qdrant, and returns `(chunks, is_empty)`. `is_empty=True` when nothing was found **or** the top score < 0.3 → **refuse** (skip the LLM).
3. Otherwise `build_prompt()` numbers the chunks `[1]…[n]` and tells the model to answer only from them.
4. `OllamaLLM.generate()` produces the answer.
5. `extract_citation_indices()` + `build_citations()` return only the sources the model actually cited.
6. Response: `{answer, citations[]}`. A Qdrant failure → 503; an Ollama failure → 503; both distinct in the logs.

**The design idea to remember:** everything the request touches (embedder, LLM) sits behind an interface and is configured by `settings`, so you swap the model or provider by changing config — not code.
