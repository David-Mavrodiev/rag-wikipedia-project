"""The Ollama client must declare its own context window.

Ollama sizes CUDA compute buffers from the context length. Left to the server
default, llama3.2's advertised maximum fails to allocate on a 6 GB card, so
whether generation worked depended on env vars set in whatever terminal
launched `ollama serve`.
"""

from unittest.mock import MagicMock, patch

import pytest
from app.core.config import Settings, settings
from app.core.llm import OllamaLLM


def _client_with(fake_module) -> OllamaLLM:
    with patch.dict("sys.modules", {"ollama": fake_module}):
        return OllamaLLM(model="llama3.2:3b", base_url="http://localhost:11434")


def _fake_ollama():
    module = MagicMock()
    module.Client.return_value.generate.return_value = {"response": "an answer"}
    return module


def test_generate_sends_the_configured_context_window():
    module = _fake_ollama()
    llm = _client_with(module)

    assert llm.generate("prompt") == "an answer"

    _, kwargs = module.Client.return_value.generate.call_args
    assert kwargs["options"]["num_ctx"] == settings.llm_num_ctx


def test_context_window_can_be_overridden_per_client():
    module = _fake_ollama()
    with patch.dict("sys.modules", {"ollama": module}):
        llm = OllamaLLM(model="llama3.2:3b", base_url="http://x", num_ctx=4096)

    llm.generate("prompt")

    _, kwargs = module.Client.return_value.generate.call_args
    assert kwargs["options"]["num_ctx"] == 4096


def test_context_window_leaves_headroom_over_the_token_budget():
    # The retrieved context alone is token_budget tokens; the prompt template,
    # the question and the generated answer all have to fit alongside it.
    assert settings.llm_num_ctx > settings.token_budget


def test_default_context_window_is_bounded():
    # An unbounded window is what caused the CUDA allocation failure.
    assert Settings().llm_num_ctx == 8192


@pytest.mark.parametrize("value", ["0", "-1", "200000"])
def test_out_of_range_context_window_is_rejected(monkeypatch, value):
    from pydantic import ValidationError

    monkeypatch.setenv("LLM_NUM_CTX", value)

    with pytest.raises(ValidationError):
        Settings()
