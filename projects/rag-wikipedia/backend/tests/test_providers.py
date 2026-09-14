"""The model registry: one value swaps the provider, and the default is unchanged.

The point of this file is not that a dict lookup works. It is that ADDING a
cloud provider changed nothing about what ships - the defaults still build the
local models the served path builds today - and that selecting the cloud one is
a single environment variable rather than an edit.

`import providers` must also work with no `gcp` extra installed, which is every
CI job and any deployment serving the local models. That is asserted here
rather than discovered at deploy time.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from app.core.embeddings import BGEEmbedder
from app.core.llm import OllamaLLM
from providers.registry import (
    DEFAULT_EMBEDDER,
    DEFAULT_LLM,
    EMBED_REGISTRY,
    LLM_REGISTRY,
    make_embedder,
    make_llm,
)

# --------------------------------------------------------------------------
# Adding a provider must not change what ships.
# --------------------------------------------------------------------------


def test_the_default_llm_is_the_one_the_served_path_builds():
    assert DEFAULT_LLM == "ollama-3b"


def test_the_default_embedder_is_the_one_the_served_path_builds():
    assert DEFAULT_EMBEDDER == "bge"


def test_the_default_llm_choice_builds_an_ollama_client(monkeypatch):
    monkeypatch.delenv("LLM_CHOICE", raising=False)
    with patch.dict("sys.modules", {"ollama": MagicMock()}):
        assert isinstance(make_llm(), OllamaLLM)


def test_the_default_embedder_choice_builds_the_local_embedder(monkeypatch):
    # sentence_transformers is patched out: this asserts WHICH class the
    # default selects, and loading a real model to learn that would cost
    # hundreds of megabytes and several seconds per run.
    monkeypatch.delenv("EMBED_CHOICE", raising=False)
    with patch.dict("sys.modules", {"sentence_transformers": MagicMock()}):
        assert isinstance(make_embedder(), BGEEmbedder)


# --------------------------------------------------------------------------
# One value swaps the provider.
# --------------------------------------------------------------------------


def test_the_llm_choice_environment_variable_selects_the_provider(monkeypatch):
    sentinel = object()
    monkeypatch.setitem(LLM_REGISTRY, "fake", lambda: sentinel)
    monkeypatch.setenv("LLM_CHOICE", "fake")

    assert make_llm() is sentinel


def test_the_embed_choice_environment_variable_selects_the_provider(monkeypatch):
    sentinel = object()
    monkeypatch.setitem(EMBED_REGISTRY, "fake", lambda: sentinel)
    monkeypatch.setenv("EMBED_CHOICE", "fake")

    assert make_embedder() is sentinel


def test_an_explicit_choice_beats_the_environment(monkeypatch):
    # The eval harness selects per run; it must not be overridden by whatever
    # the shell happens to export.
    chosen = object()
    monkeypatch.setitem(LLM_REGISTRY, "explicit", lambda: chosen)
    monkeypatch.setitem(LLM_REGISTRY, "from-env", lambda: object())
    monkeypatch.setenv("LLM_CHOICE", "from-env")

    assert make_llm("explicit") is chosen


# --------------------------------------------------------------------------
# An unknown name fails loudly, at construction, naming the options.
# --------------------------------------------------------------------------


def test_an_unknown_llm_choice_is_rejected_and_lists_the_options(monkeypatch):
    monkeypatch.setenv("LLM_CHOICE", "gpt-9")

    with pytest.raises(ValueError, match="Unknown LLM_CHOICE") as excinfo:
        make_llm()
    assert "ollama-3b" in str(excinfo.value)


def test_an_unknown_embed_choice_is_rejected_and_lists_the_options(monkeypatch):
    monkeypatch.setenv("EMBED_CHOICE", "nope")

    with pytest.raises(ValueError, match="Unknown EMBED_CHOICE") as excinfo:
        make_embedder()
    assert "bge" in str(excinfo.value)


# --------------------------------------------------------------------------
# The cloud provider is registered, and costs nothing until it is chosen.
# --------------------------------------------------------------------------


def test_vertex_is_registered_for_both_roles():
    # The claim a reviewer checks by reading the registry: a second cloud
    # provider exists behind the same interfaces.
    assert "vertex" in LLM_REGISTRY
    assert "vertex" in EMBED_REGISTRY


def test_importing_the_registry_does_not_require_the_gcp_extra():
    # google-cloud-aiplatform is NOT installed in CI. If this module imported
    # it at module level, every job would fail at import rather than at the
    # point someone selected the provider.
    import importlib

    assert importlib.import_module("providers.registry") is not None


def test_selecting_vertex_without_the_sdk_fails_at_construction_not_at_import():
    # The failure mode that matters: choosing a provider whose SDK is missing
    # must raise where the choice was made, with the SDK named.
    with patch.dict("sys.modules", {"vertexai": None}):
        with pytest.raises(ImportError):
            make_llm("vertex")
