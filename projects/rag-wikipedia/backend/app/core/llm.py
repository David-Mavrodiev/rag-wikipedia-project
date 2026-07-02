from __future__ import annotations

from abc import ABC, abstractmethod


class LLM(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class OllamaLLM(LLM):
    def __init__(self, model: str, base_url: str):
        import ollama

        self._model = model
        self._client = ollama.Client(host=base_url)

    def generate(self, prompt: str) -> str:
        response = self._client.generate(model=self._model, prompt=prompt)
        return response["response"]
