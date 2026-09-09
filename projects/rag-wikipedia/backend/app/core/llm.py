from __future__ import annotations

from abc import ABC, abstractmethod


class LLM(ABC):
    """The one call the chain makes of a generator.

    CONTRACT - an implementation MUST default to deterministic decoding: the
    provider's temperature set to zero, and its sampling seed pinned wherever
    one is exposed.

    This is a property of the interface, not a preference of any one adapter,
    because the whole point of having the interface is that adapters are
    interchangeable. A grounded answer here is an extraction task - say what
    the retrieved context supports, cite it, otherwise decline - and sampling
    buys nothing for that while costing two things that matter:

    * Comparability. Retrieval is scored against fixed suites, but any
      comparison that reads the generated TEXT - one engine against another,
      one provider against another, groundedness before and after a prompt
      change - cannot separate a real regression from resampling if the
      baseline moves on its own. Measured here: the same question returned one
      citation, then two, with nothing changed between the runs.
    * Reproducibility. A user reporting a bad answer should be able to hand
      over the question and get the same bad answer back.

    Honest limit: greedy decoding removes SAMPLING variance, not every source
    of variance. Different hardware, a different server build, or a different
    batching decision inside the runtime can still diverge. This makes runs
    comparable; it does not make them bit-identical.
    """

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
            options={
                "num_ctx": self._num_ctx,
                # The LLM contract above. Ollama's own default is 0.8, so
                # leaving these out was the only reason the served path
                # sampled while every adapter in providers.py did not.
                # seed is redundant at temperature 0 (greedy decoding does not
                # draw) and is sent anyway: it states the intent, and it keeps
                # the request deterministic if anyone raises the temperature.
                "temperature": 0.0,
                "seed": 0,
            },
        )
        return response["response"]
