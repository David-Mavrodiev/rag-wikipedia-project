# =============================================================================
# providers.py — LLM / Embedder REGISTRY  (DORMANT capability · Option A)
# =============================================================================
#
# STATUS: this whole file is commented out on purpose. It does NOTHING today.
# Importing it is a no-op (a module of comments = an empty module), so the app
# keeps running exactly as-is on the local BGEEmbedder + OllamaLLM. Nothing here
# executes, imports, or loads a dependency until you deliberately activate it.
#
# WHAT IT GIVES YOU — a ONE-VALUE swap for ANY model.
#   Switching Ollama-3B -> Ollama-1B, or Ollama -> Azure OpenAI / OpenAI / any
#   OpenAI-compatible server, is the SAME single change: set LLM_CHOICE (for the
#   generator) or EMBED_CHOICE (for the embedder). Adding a brand-new LLM is ONE
#   line in the registry. Nothing else changes — retrieval.py, prompt.py,
#   citations.py and query.py depend only on the LLM / Embedder interfaces.
#
# -----------------------------------------------------------------------------
# HOW TO ACTIVATE  (e.g. to point the project at a client's LLM on day one)
# -----------------------------------------------------------------------------
#   1) Uncomment this file (strip the leading "# " from the code lines below).
#   2) Add the SDK to backend/pyproject.toml:   "openai>=1.30",
#      then re-sync:   uv sync --all-extras
#   3) Wire the two factories in app/api/query.py — replace the bodies:
#          from app.core.providers import make_embedder, make_llm   # add import
#          @lru_cache(maxsize=1)
#          def _embedder(): return make_embedder()    # was: BGEEmbedder(...)
#          @lru_cache(maxsize=1)
#          def _llm():      return make_llm()           # was: OllamaLLM(...)
#   4) Pick your models with ONE value each:
#          LLM_CHOICE   = ollama-3b | ollama-1b | azure | openai   (default: ollama-3b)
#          EMBED_CHOICE = bge | azure | openai                     (default: bge)
#      ...plus the credentials for whichever preset you chose (see ENV VARS).
#   5) If you change the EMBEDDER (not just the LLM), set EMBED_DIM and
#      RE-INGEST into a NEW collection. Do NOT assume 1536: bge-small is 384,
#      text-embedding-3-small is 1536, text-embedding-3-large is 3072, an Azure
#      deployment can serve any model, and some APIs accept a requested output
#      dimension. Vectors of different dimensions are not interchangeable, so a
#      wrong value builds a collection that rejects the vectors you produce.
#      Verify against a REAL embedding before creating the collection:
#          probe = embedder.embed("dimension probe")
#          assert len(probe) == int(os.environ["EMBED_DIM"]), len(probe)
#      (see the vectorstore note at the bottom of this file)
#
# -----------------------------------------------------------------------------
# ENV VARS
# -----------------------------------------------------------------------------
#   LLM_CHOICE / EMBED_CHOICE          -> pick the preset (this is the whole point)
#   OLLAMA_URL, LLM_MODEL              -> local Ollama (defaults already work)
#   AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION,
#     AZURE_CHAT_DEPLOYMENT, AZURE_EMBED_DEPLOYMENT     -> Azure OpenAI
#   OPENAI_API_KEY, OPENAI_MODEL, OPENAI_EMBED_MODEL, OPENAI_BASE_URL
#                                       -> OpenAI, or ANY OpenAI-compatible server
#                                          (vLLM, Ollama /v1, Together, Groq, Mistral, ...)
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
# # Adapters — each implements the SAME interface, so they are interchangeable.
# # (Ollama already exists in llm.py; these add the hosted providers.)
# # ---------------------------------------------------------------------------
# class AzureOpenAILLM(LLM):                                # generator backed by Azure OpenAI chat completions
#     def __init__(self, deployment: str, endpoint: str, api_key: str, api_version: str = "2024-06-01"):
#         from openai import AzureOpenAI                    # lazy import: only loaded if this preset is used
#         self._deployment = deployment                     # Azure quirk: call the DEPLOYMENT name, not "gpt-4o"
#         self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
#     def generate(self, prompt: str) -> str:               # same signature as OllamaLLM -> nothing else changes
#         r = self._client.chat.completions.create(
#             model=self._deployment,
#             messages=[{"role": "user", "content": prompt}],   # build_prompt() already embeds the system rules
#             temperature=0.0,                              # deterministic: a grounded RAG answer shouldn't be creative
#         )
#         return r.choices[0].message.content or ""         # pull the text; "" guards a null content
#
# class OpenAILLM(LLM):                                     # generator backed by OpenAI OR any OpenAI-compatible server
#     def __init__(self, model: str, api_key: str, base_url: str | None = None):
#         from openai import OpenAI
#         self._model = model                               # "gpt-4o-mini", or a served open-source model name
#         self._client = OpenAI(api_key=api_key, base_url=base_url)  # base_url -> vLLM / Ollama /v1 / Together / Groq...
#     def generate(self, prompt: str) -> str:
#         r = self._client.chat.completions.create(
#             model=self._model,
#             messages=[{"role": "user", "content": prompt}],
#             temperature=0.0,
#         )
#         return r.choices[0].message.content or ""
#
# def _ordered_embeddings(response, texts: list[str]) -> list[list[float]]:
#     """Return the batch's embeddings in INPUT order, mapping by item.index.
#
#     NEVER trust response order. A provider may return batch items out of
#     order; zipping by position would then attach a vector to the WRONG chunk
#     text. That corruption is silent — ingestion succeeds, retrieval returns
#     confident nonsense, and nothing surfaces the mismatch. Both Azure and
#     OpenAI document `index` as the item's position in the input list.
#     Missing, duplicate and out-of-range indices are rejected loudly.
#     """
#     out: list[list[float] | None] = [None] * len(texts)
#     for item in response.data:
#         if not 0 <= item.index < len(texts):
#             raise ValueError(f"embedding index {item.index} out of range for {len(texts)} inputs")
#         if out[item.index] is not None:
#             raise ValueError(f"duplicate embedding index {item.index}")
#         out[item.index] = item.embedding
#     missing = [i for i, vector in enumerate(out) if vector is None]
#     if missing:
#         raise ValueError(f"provider returned no embedding for inputs {missing}")
#     return out  # type: ignore[return-value]
#
#
# class AzureOpenAIEmbedder(Embedder):                      # embeddings via Azure OpenAI (dimension varies!)
#     def __init__(self, deployment: str, endpoint: str, api_key: str, api_version: str = "2024-06-01"):
#         from openai import AzureOpenAI
#         self._deployment = deployment
#         self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
#     def embed(self, text: str) -> list[float]:            # one string -> one vector
#         return self._client.embeddings.create(model=self._deployment, input=text).data[0].embedding
#     def embed_batch(self, texts: list[str]) -> list[list[float]]:   # many strings -> many vectors, one call
#         r = self._client.embeddings.create(model=self._deployment, input=texts)
#         return _ordered_embeddings(r, texts)              # map by index, not by arrival order
#
# class OpenAIEmbedder(Embedder):                           # embeddings via OpenAI OR an OpenAI-compatible server
#     def __init__(self, model: str, api_key: str, base_url: str | None = None):
#         from openai import OpenAI
#         self._model = model
#         self._client = OpenAI(api_key=api_key, base_url=base_url)
#     def embed(self, text: str) -> list[float]:
#         return self._client.embeddings.create(model=self._model, input=text).data[0].embedding
#     def embed_batch(self, texts: list[str]) -> list[list[float]]:
#         r = self._client.embeddings.create(model=self._model, input=texts)
#         return _ordered_embeddings(r, texts)              # map by index, not by arrival order
#
#
# # ---------------------------------------------------------------------------
# # THE REGISTRY — a friendly name -> a zero-arg factory that builds a ready-to-
# # use model. Swapping = change LLM_CHOICE / EMBED_CHOICE. Adding a model = ONE
# # new line. Factories are lambdas, so nothing is built (and no credential is
# # read) until you actually SELECT that preset and call the factory.
# # ---------------------------------------------------------------------------
# LLM_REGISTRY = {
#     "ollama-3b": lambda: OllamaLLM("llama3.2:3b", os.getenv("OLLAMA_URL", "http://localhost:11434")),
#     "ollama-1b": lambda: OllamaLLM("llama3.2:1b", os.getenv("OLLAMA_URL", "http://localhost:11434")),
#     "azure":     lambda: AzureOpenAILLM(os.environ["AZURE_CHAT_DEPLOYMENT"],
#                                         os.environ["AZURE_OPENAI_ENDPOINT"],
#                                         os.environ["AZURE_OPENAI_API_KEY"],
#                                         os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")),
#     "openai":    lambda: OpenAILLM(os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
#                                    os.environ["OPENAI_API_KEY"],
#                                    os.getenv("OPENAI_BASE_URL") or None),
#     # e.g. "mistral": lambda: OpenAILLM("mistral-large-latest", os.environ["MISTRAL_API_KEY"], "https://api.mistral.ai/v1"),
#     # <- ADD ANY NEW LLM (open-source or not) AS ONE LINE HERE
# }
#
# EMBED_REGISTRY = {
#     "bge":    lambda: BGEEmbedder(os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")),            # local, 384-d
#     "azure":  lambda: AzureOpenAIEmbedder(os.environ["AZURE_EMBED_DEPLOYMENT"],
#                                           os.environ["AZURE_OPENAI_ENDPOINT"],
#                                           os.environ["AZURE_OPENAI_API_KEY"],
#                                           os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")),
#     "openai": lambda: OpenAIEmbedder(os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
#                                      os.environ["OPENAI_API_KEY"],
#                                      os.getenv("OPENAI_BASE_URL") or None),
# }
# # DIMENSIONS ARE NOT UNIFORM. bge-small=384, text-embedding-3-small=1536,
# # text-embedding-3-large=3072, and an Azure deployment serves whatever model
# # it was created with. For any non-"bge" choice: probe the real dimension
# # (step 5), set EMBED_DIM to it, and re-ingest into a NEW collection.
#
#
# def make_llm() -> LLM:                                    # build the LLM named by LLM_CHOICE (default: ollama-3b)
#     choice = os.getenv("LLM_CHOICE", "ollama-3b")
#     if choice not in LLM_REGISTRY:                        # clear error if someone sets an unknown name
#         raise ValueError(f"Unknown LLM_CHOICE {choice!r}; options: {sorted(LLM_REGISTRY)}")
#     return LLM_REGISTRY[choice]()                         # call the factory -> a ready LLM
#
#
# def make_embedder() -> Embedder:                          # build the Embedder named by EMBED_CHOICE (default: bge)
#     choice = os.getenv("EMBED_CHOICE", "bge")
#     if choice not in EMBED_REGISTRY:
#         raise ValueError(f"Unknown EMBED_CHOICE {choice!r}; options: {sorted(EMBED_REGISTRY)}")
#     return EMBED_REGISTRY[choice]()
#
#
# # ---------------------------------------------------------------------------
# # vectorstore.py note (only relevant if you switch the EMBEDDER's dimension):
# # ensure_collection() hardcodes EXPECTED_DIM = 384 and raises if it differs.
# # To use a hosted embedder, make the dimension configurable:
# #     EXPECTED_DIM = int(os.getenv("EMBED_DIM", "384"))
# # then set EMBED_DIM to the dimension you PROBED (step 5) — not an assumed
# # 1536 — and re-ingest into a fresh collection. Keeping the assert in
# # ensure_collection() is what turns a wrong EMBED_DIM into a loud failure at
# # startup instead of a confusing rejection mid-ingest.
# # =============================================================================
