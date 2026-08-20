from __future__ import annotations

from abc import ABC, abstractmethod


class LLM(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class OllamaLLM(LLM):
    """Ollama client that declares the context window it needs.

    Ollama sizes its CUDA compute buffers from the context length, and
    llama3.2 advertises a very large maximum. Left to the server default, a
    6 GB card fails to start the model:

        cudaMalloc failed: out of memory
        ggml_gallocr_reserve_n_impl: failed to allocate CUDA0 buffer of size 969998336

    That made generation depend on environment set in whatever terminal
    happened to launch `ollama serve` - a dependency this app never declared.
    Sending num_ctx per request pins it here instead, so the API behaves the
    same against any Ollama regardless of how it was started.
    """

    def __init__(self, model: str, base_url: str, num_ctx: int | None = None):
        import ollama

        from app.core.config import settings

        self._model = model
        self._num_ctx = num_ctx if num_ctx is not None else settings.llm_num_ctx
        self._client = ollama.Client(host=base_url)

    def generate(self, prompt: str) -> str:
        response = self._client.generate(
            model=self._model,
            prompt=prompt,
            options={"num_ctx": self._num_ctx},
        )
        return response["response"]
