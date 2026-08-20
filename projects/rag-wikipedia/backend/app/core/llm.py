from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from opentelemetry import trace


class LLM(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


def _field(response: Any, name: str) -> Any:
    # Older ollama clients returned a plain dict, newer ones a model object.
    if isinstance(response, dict):
        return response.get(name)
    return getattr(response, name, None)


def _record_generation(response: Any) -> None:
    """Attach Ollama's own timings to whatever span is currently active.

    These come back on every response and were being discarded with the rest of
    the payload. They are the only way to tell "the model was busy with someone
    else" (load) from "the model was slow for us" (eval) - which, on a
    deployment pinned to a single Ollama replica, is the difference between
    adding capacity and shortening the prompt.

    Written onto the current span rather than a new one so `LLM.generate` keeps
    its `-> str` contract: widening the return type would push an Ollama-shaped
    detail into the interface that exists to hide exactly that.
    """
    span = trace.get_current_span()
    if not span.is_recording():
        return

    for attribute, key in (
        ("gen_ai.usage.input_tokens", "prompt_eval_count"),
        ("gen_ai.usage.output_tokens", "eval_count"),
    ):
        value = _field(response, key)
        if value is not None:
            span.set_attribute(attribute, int(value))

    # Ollama reports durations in nanoseconds; milliseconds are what anyone
    # comparing this against a request timeline is thinking in.
    for attribute, key in (
        ("rag.llm.total_ms", "total_duration"),
        ("rag.llm.load_ms", "load_duration"),
        ("rag.llm.prompt_eval_ms", "prompt_eval_duration"),
        ("rag.llm.eval_ms", "eval_duration"),
    ):
        value = _field(response, key)
        if value is not None:
            span.set_attribute(attribute, int(value) / 1_000_000)


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
        _record_generation(response)
        return response["response"]
