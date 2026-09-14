"""Vertex AI implementations of the `LLM` and `Embedder` contracts.

WHY THIS IS NOT IN `app/`. The layering test parses `app/` with `ast.walk`,
which sees imports anywhere in a file - including inside a function body - so a
lazily imported cloud SDK is still a violation there. That is deliberate: the
claim is that the core is cloud-free by construction, and a claim that only
holds at module level is not the claim. `app/core/providers.py` documents this
registry and is entirely commented out for the same reason. Implementations
that need an SDK live here, behind the interface, and are imported lazily.

WHAT "CLOUD-AGNOSTIC" MEANS HERE, CONCRETELY. `retrieval.py`, `prompt.py`,
`citations.py` and `query.py` name no provider. They depend on `LLM` and
`Embedder`. This file adds a second implementation of each. Selecting it is one
environment variable (see `registry.py`); nothing else in the chain changes.

AUTHENTICATION is Application Default Credentials - `gcloud auth
application-default login`, or a service account via
GOOGLE_APPLICATION_CREDENTIALS. There is no API key to pass, which is why
neither class takes one.

VERIFIED AGAINST MOCKS, NOT AGAINST BILLED ENDPOINTS. These adapters are
covered by `tests/test_vertex.py`, which patches `sys.modules` the way
`tests/test_llm.py` does for ollama, so the contracts are enforced in CI with
no GCP account and no `gcp` extra installed. What that CANNOT prove is the
shape of a real response, and one field is worth naming: `seed` is sent in
`generation_config` to satisfy the LLM contract's "pin the sampling seed
wherever one is exposed". Gemini accepts it; it is the single parameter here
that has not been exercised against a live endpoint. If a future API version
rejects it, that is where to look first.
"""

from __future__ import annotations

from app.core.embeddings import Embedder
from app.core.llm import LLM

# Defaults chosen so the registry entries read as one line. Both are
# overridable per environment - see registry.py.
DEFAULT_LLM_MODEL = "gemini-2.0-flash"
DEFAULT_EMBED_MODEL = "text-embedding-005"
DEFAULT_LOCATION = "us-central1"

# Vertex caps instances per text-embedding request. A caller asking for more
# than this is not an error - it is a request the provider would reject - so
# the window is clamped rather than passed through.
MAX_EMBED_INSTANCES = 250


class VertexLLM(LLM):
    """Gemini on Vertex AI, decoding greedily.

    The LLM contract requires deterministic decoding from every adapter, not
    just from the one that happens to ship. Vertex defaults to temperature 1.0,
    so leaving `generation_config` off would mean that choosing this provider
    silently changed answer behaviour - precisely what an interchangeable
    interface exists to prevent.
    """

    def __init__(
        self,
        model: str = DEFAULT_LLM_MODEL,
        project: str | None = None,
        location: str = DEFAULT_LOCATION,
    ):
        # Lazy: `import providers.vertex` must work wherever the `gcp` extra is
        # not installed, which is every CI job and any deployment serving the
        # local models. The SDK is only needed once this adapter is CHOSEN.
        import vertexai
        from vertexai.generative_models import GenerativeModel

        vertexai.init(project=project, location=location)
        self._model = GenerativeModel(model)
        # A plain dict, not GenerationConfig: the SDK accepts either, and a
        # dict keeps the decoding contract readable in one place and assertable
        # in a test without constructing an SDK object.
        self._generation_config = {"temperature": 0.0, "seed": 0}

    def generate(self, prompt: str) -> str:
        response = self._model.generate_content(
            prompt,
            generation_config=self._generation_config,
        )
        # `or ""` guards a safety-blocked or empty candidate, which surfaces as
        # a null text rather than an exception. The chain downstream treats an
        # empty generation as a refusal; it must not see None.
        return response.text or ""


class VertexEmbedder(Embedder):
    """Vertex text embeddings, with the dimension probed rather than declared.

    text-embedding-005 is 768-d against bge-small's 384. That difference is the
    reason `dim` is part of the contract: `ensure_collection(dim)` compares the
    embedder's dimension against the size the collection was CREATED at and
    refuses a mismatch naming both numbers. So switching to this embedder does
    not corrupt the existing 384-d collection - it stops at startup, and a
    re-ingest into a NEW collection is a deliberate act.
    """

    def __init__(
        self,
        model: str = DEFAULT_EMBED_MODEL,
        project: str | None = None,
        location: str = DEFAULT_LOCATION,
    ):
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        vertexai.init(project=project, location=location)
        self._model = TextEmbeddingModel.from_pretrained(model)
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        # PROBED, per the Embedder contract, and cached: callers read this once
        # per ingest, so the billed call happens once per process, not once per
        # chunk. A declared dimension is one that can be declared wrongly.
        if self._dim is None:
            self._dim = len(self.embed("dimension probe"))
        return self._dim

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str], *, batch_size: int | None = None) -> list[list[float]]:
        """Embed many texts, in input order.

        `batch_size` is HONOURED, not ignored: `pipeline/tasks.py` calls
        `embed_batch(..., batch_size=64)`, so an adapter that drops the keyword
        raises TypeError on the first real ingest.

        ORDER. Unlike the OpenAI-shaped APIs, Vertex returns a positional list
        with no per-item index, so there is no index to map by - the only
        available guarantee is position. That makes the count the one thing
        worth checking: a short or long response would otherwise zip a vector
        onto the WRONG chunk text, and that corruption is silent. Ingestion
        succeeds, retrieval returns confident nonsense, and nothing surfaces it.
        """
        if not texts:
            return []
        window_size = min(batch_size or len(texts), MAX_EMBED_INSTANCES)
        out: list[list[float]] = []
        for start in range(0, len(texts), window_size):
            window = texts[start : start + window_size]
            embeddings = self._model.get_embeddings(window)
            if len(embeddings) != len(window):
                raise ValueError(
                    f"Vertex returned {len(embeddings)} embeddings for {len(window)} inputs; "
                    "the response cannot be aligned to its inputs by position."
                )
            out.extend(list(embedding.values) for embedding in embeddings)
        return out
