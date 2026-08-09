# =============================================================================
# providers.py  —  LLM / Embedder provider registry  (DORMANT capability)
# =============================================================================
#
# STATUS: This entire file is commented out on purpose. It does NOTHING today.
# Importing it is a no-op (a module of comments is an empty module), so the app
# keeps running exactly as-is on the local BGEEmbedder + OllamaLLM. Nothing here
# executes, imports, or loads a dependency until you deliberately activate it.
#
# WHAT IT IS: a real, LLM-agnostic provider registry. Because the whole app
# depends only on the `Embedder` and `LLM` interfaces (see embeddings.py / llm.py),
# swapping the model is a configuration choice, not a code change. This file makes
# that swap real for: Ollama (local), Azure OpenAI, vanilla OpenAI, AND — via an
# OpenAI-compatible base_url — essentially ANY open-source model server that
# speaks the OpenAI protocol (vLLM, Ollama's /v1, Together, Groq, Mistral, ...).
#
# -----------------------------------------------------------------------------
# HOW TO ACTIVATE (later, when you actually want to swap providers)
# -----------------------------------------------------------------------------
#   1) Uncomment this file (strip the leading "# " from the code lines below).
#   2) Add the SDK to backend/pyproject.toml dependencies:   "openai>=1.30",
#      then re-sync:   uv sync --all-extras
#   3) Choose providers via environment variables (see ENV VARS below).
#   4) Wire the two factories in app/api/query.py — replace the bodies:
#          from app.core.providers import make_embedder, make_llm   # add import
#          @lru_cache(maxsize=1)
#          def _embedder(): return make_embedder()   # was: BGEEmbedder(...)
#          @lru_cache(maxsize=1)
#          def _llm():      return make_llm()          # was: OllamaLLM(...)
#   5) If you switch the EMBEDDER (not just the LLM), you MUST re-ingest:
#      the vector dimension changes (bge-small = 384; OpenAI
#      text-embedding-3-small = 1536), and Qdrant vectors of different
#      dimensions are NOT interchangeable. Also relax the hardcoded 384 guard
#      in vectorstore.py (see the note at the very bottom of this file).
#
# -----------------------------------------------------------------------------
# ENV VARS (read directly here, so activation needs no edits to config.py)
# -----------------------------------------------------------------------------
#   LLM_PROVIDER   = ollama | azure | openai            (default: ollama)
#   EMBED_PROVIDER = bge    | azure | openai            (default: bge)
#
#   # Ollama (local, default):
#   OLLAMA_URL=http://localhost:11434     LLM_MODEL=llama3.2:3b
#   EMBED_MODEL=BAAI/bge-small-en-v1.5
#
#   # Azure OpenAI:
#   AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
#   AZURE_OPENAI_API_KEY=<key>            AZURE_OPENAI_API_VERSION=2024-06-01
#   AZURE_CHAT_DEPLOYMENT=<your-gpt-deployment>
#   AZURE_EMBED_DEPLOYMENT=<your-embedding-deployment>
#
#   # OpenAI (or ANY OpenAI-compatible server via OPENAI_BASE_URL):
#   OPENAI_API_KEY=<key>                  OPENAI_MODEL=gpt-4o-mini
#   OPENAI_EMBED_MODEL=text-embedding-3-small
#   OPENAI_BASE_URL=                      # empty=OpenAI; else e.g. http://localhost:8000/v1 (vLLM), etc.
#
# WHY THIS IS "REAL" (not a stub): adding a new LLM — open-source or not — is a
# single branch in make_llm() (or a single OPENAI_BASE_URL if it's OpenAI-
# compatible). retrieval.py, prompt.py, citations.py and query.py never change.
# =============================================================================
#
# from __future__ import annotations
# import os
#
# from app.core.embeddings import BGEEmbedder, Embedder   # local embedder + the interface
# from app.core.llm import LLM, OllamaLLM                  # local LLM + the interface
#
#
# # ---------------------------------------------------------------------------
# # LLM adapters (each implements the same LLM.generate(prompt) -> str contract)
# # ---------------------------------------------------------------------------
# class AzureOpenAILLM(LLM):                                # LLM backed by Azure OpenAI chat completions
#     def __init__(self, deployment: str, endpoint: str, api_key: str, api_version: str = "2024-06-01"):
#         from openai import AzureOpenAI                    # lazy import: only needed if this provider is used
#         self._deployment = deployment                     # Azure quirk: you call the DEPLOYMENT name, not "gpt-4o"
#         self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
#
#     def generate(self, prompt: str) -> str:               # same signature as OllamaLLM -> nothing else changes
#         resp = self._client.chat.completions.create(
#             model=self._deployment,                       # "model" == the deployment name on Azure
#             messages=[{"role": "user", "content": prompt}],  # build_prompt() already embeds the system rules
#             temperature=0.0,                              # deterministic: a grounded RAG answer shouldn't be creative
#         )
#         return resp.choices[0].message.content or ""      # pull the text; "" guards a null content
#
#
# class OpenAILLM(LLM):                                     # LLM backed by OpenAI *or any OpenAI-compatible server*
#     def __init__(self, model: str, api_key: str, base_url: str | None = None):
#         from openai import OpenAI                         # lazy import
#         self._model = model                               # e.g. "gpt-4o-mini", or a served open-source model name
#         # base_url=None -> api.openai.com. Set it to point at vLLM / Ollama /v1 / Together / Groq / Mistral / etc.
#         self._client = OpenAI(api_key=api_key, base_url=base_url)
#
#     def generate(self, prompt: str) -> str:
#         resp = self._client.chat.completions.create(
#             model=self._model,
#             messages=[{"role": "user", "content": prompt}],
#             temperature=0.0,
#         )
#         return resp.choices[0].message.content or ""
#
#
# # ---------------------------------------------------------------------------
# # Embedder adapters (each implements embed() and embed_batch())
# # ---------------------------------------------------------------------------
# class AzureOpenAIEmbedder(Embedder):                      # embeddings via Azure OpenAI
#     def __init__(self, deployment: str, endpoint: str, api_key: str, api_version: str = "2024-06-01"):
#         from openai import AzureOpenAI
#         self._deployment = deployment
#         self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
#
#     def embed(self, text: str) -> list[float]:            # one string -> one vector
#         return self._client.embeddings.create(model=self._deployment, input=text).data[0].embedding
#
#     def embed_batch(self, texts: list[str]) -> list[list[float]]:   # many strings -> many vectors, one call
#         resp = self._client.embeddings.create(model=self._deployment, input=texts)
#         return [item.embedding for item in resp.data]     # Azure preserves input order
#
#
# class OpenAIEmbedder(Embedder):                           # embeddings via OpenAI *or an OpenAI-compatible server*
#     def __init__(self, model: str, api_key: str, base_url: str | None = None):
#         from openai import OpenAI
#         self._model = model
#         self._client = OpenAI(api_key=api_key, base_url=base_url)
#
#     def embed(self, text: str) -> list[float]:
#         return self._client.embeddings.create(model=self._model, input=text).data[0].embedding
#
#     def embed_batch(self, texts: list[str]) -> list[list[float]]:
#         resp = self._client.embeddings.create(model=self._model, input=texts)
#         return [item.embedding for item in resp.data]
#
#
# # ---------------------------------------------------------------------------
# # The factories (the "real swap"): pick a provider by name from the env.
# # Add a new LLM = one extra branch here. That's the whole extension point.
# # ---------------------------------------------------------------------------
# def make_llm() -> LLM:                                    # returns whichever LLM the env selects
#     provider = os.getenv("LLM_PROVIDER", "ollama").lower()
#     if provider == "ollama":                              # local, default (unchanged behavior)
#         return OllamaLLM(model=os.getenv("LLM_MODEL", "llama3.2:3b"),
#                          base_url=os.getenv("OLLAMA_URL", "http://localhost:11434"))
#     if provider == "azure":                               # Azure OpenAI
#         return AzureOpenAILLM(os.environ["AZURE_CHAT_DEPLOYMENT"],
#                               os.environ["AZURE_OPENAI_ENDPOINT"],
#                               os.environ["AZURE_OPENAI_API_KEY"],
#                               os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"))
#     if provider == "openai":                              # OpenAI, or any OpenAI-compatible endpoint via OPENAI_BASE_URL
#         return OpenAILLM(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
#                          api_key=os.environ["OPENAI_API_KEY"],
#                          base_url=os.getenv("OPENAI_BASE_URL") or None)
#     raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (use ollama | azure | openai)")
#
#
# def make_embedder() -> Embedder:                          # returns whichever Embedder the env selects
#     provider = os.getenv("EMBED_PROVIDER", "bge").lower()
#     if provider == "bge":                                 # local sentence-transformers, default (384-d)
#         return BGEEmbedder(model_name=os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
#     if provider == "azure":                               # Azure OpenAI embeddings (e.g. 1536-d) -> re-ingest needed
#         return AzureOpenAIEmbedder(os.environ["AZURE_EMBED_DEPLOYMENT"],
#                                    os.environ["AZURE_OPENAI_ENDPOINT"],
#                                    os.environ["AZURE_OPENAI_API_KEY"],
#                                    os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"))
#     if provider == "openai":                              # OpenAI embeddings (or compatible), e.g. 1536-d -> re-ingest
#         return OpenAIEmbedder(model=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
#                               api_key=os.environ["OPENAI_API_KEY"],
#                               base_url=os.getenv("OPENAI_BASE_URL") or None)
#     raise ValueError(f"Unknown EMBED_PROVIDER: {provider!r} (use bge | azure | openai)")
#
#
# # ---------------------------------------------------------------------------
# # vectorstore.py note (only relevant if you switch the EMBEDDER's dimension):
# # ensure_collection() currently hardcodes EXPECTED_DIM = 384 and raises if the
# # dim differs. To use a 1536-d embedder (Azure/OpenAI), make the dimension
# # configurable, e.g. read an EMBED_DIM env var:
# #
# #     EXPECTED_DIM = int(os.getenv("EMBED_DIM", "384"))
# #
# # then set EMBED_DIM=1536 and re-ingest into a fresh collection.
# # =============================================================================
