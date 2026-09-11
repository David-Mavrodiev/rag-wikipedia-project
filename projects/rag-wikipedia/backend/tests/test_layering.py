"""`app/` is framework-free and cloud-free. This is what keeps it that way.

The project's claim is that the chain is explicit: chunking, retrieval, the
prompt contract, refusal and citations are readable functions rather than
configuration handed to a framework. Its other claim is that the model, the
agent runtime and the cloud are all swappable, because each sits behind an
interface that `app/` depends on and no implementation detail leaks past.

Both claims are easy to write in a README and easy to break in a single import.
A LangGraph import in `core/`, or a `google.cloud` import in `api/`, would make
them false while every other test still passed. So the claims are asserted here
instead of described somewhere.

An ALLOWLIST, not a denylist, on purpose. A denylist only catches the libraries
someone thought to forbid, and rots the moment a new one appears. An allowlist
fails on anything unexpected and forces the addition to be a decision made in a
diff someone reviews.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
DIRECT_ENGINE = Path(__file__).resolve().parents[1] / "engines" / "direct.py"
ENGINE_REGISTRY_FILE = Path(__file__).resolve().parents[1] / "engines" / "registry.py"

# The packages `app/` is allowed to reach for. Every one of these is a BASE
# dependency, so the serving path never needs an optional extra installed.
#
# Adding a name here is a deliberate widening of what the core depends on.
# Adding an agent framework or a cloud SDK is not a widening but a reversal, and
# test_the_allowlist_is_not_quietly_widened below refuses it.
ALLOWED = frozenset(
    {
        "fastapi",
        "ollama",
        "pydantic",
        "pydantic_settings",
        "qdrant_client",
        "redis",
        "sentence_transformers",
        "starlette",
        "tiktoken",
    }
)

# Packages whose presence in `app/` would contradict the architecture: agent and
# RAG frameworks, and cloud provider SDKs. Implementations that need these live
# behind an interface, in their own package, imported lazily.
FORBIDDEN_PREFIXES = (
    "langchain",
    "langgraph",
    "llama_index",
    "haystack",
    "semantic_kernel",
    "google",
    "googleapiclient",
    "vertexai",
    "azure",
    "boto3",
    "botocore",
    "openai",
    "anthropic",
)

# Packages that are part of this project rather than third party.
LOCAL = frozenset({"app", "pipeline", "eval", "engines", "tests"})


def _imports() -> dict[str, set[str]]:
    """Map each top-level module imported under `app/` to the files importing it.

    Parsed rather than grepped: a comment mentioning langgraph is not an import,
    and `providers.py` is an entire file of those. AST sees what Python sees.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import, which is local by definition.
                names = [node.module] if node.level == 0 and node.module else []
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                found.setdefault(top, set()).add(path.relative_to(APP.parent).as_posix())
    return found


def _third_party() -> dict[str, set[str]]:
    return {
        name: files
        for name, files in _imports().items()
        if name not in sys.stdlib_module_names and name not in LOCAL
    }


def test_app_imports_nothing_outside_the_allowlist():
    unexpected = {name: sorted(files) for name, files in _third_party().items()
                  if name not in ALLOWED}
    assert not unexpected, (
        f"`app/` imports packages that are not on the allowlist: {unexpected}.\n"
        "If this is a framework or a cloud SDK it does not belong in app/ at all - "
        "put the implementation behind the existing interface, in its own package, "
        "and import the SDK lazily. If it genuinely belongs, add it to ALLOWED with "
        "a reason."
    )


def test_app_imports_no_framework_or_cloud_sdk():
    # Redundant against the allowlist by construction, and kept anyway: this is
    # the assertion that names WHY, so a failure reads as an architecture
    # violation rather than as a list that needs updating.
    offenders = {
        name: sorted(files)
        for name, files in _third_party().items()
        if name.startswith(FORBIDDEN_PREFIXES)
    }
    assert not offenders, (
        f"`app/` imports an agent framework or a cloud SDK: {offenders}.\n"
        "The core is framework-agnostic and cloud-agnostic by construction, not "
        "by intention."
    )


def test_the_allowlist_is_not_quietly_widened():
    # Guards the guard. The cheapest way to make the two tests above pass is to
    # add the offending package to ALLOWED, which would turn this file into
    # decoration. A framework can never be an acceptable entry.
    smuggled = sorted(name for name in ALLOWED if name.startswith(FORBIDDEN_PREFIXES))
    assert not smuggled, (
        f"ALLOWED contains {smuggled}, which the architecture forbids in app/. "
        "A failing layering test is not fixed by widening the allowlist."
    )


def _third_party_in(path: Path) -> set[str]:
    """Third-party top-level modules imported by a single file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n for n in names if n not in sys.stdlib_module_names and n not in LOCAL}


def test_the_direct_engine_stays_framework_free():
    # `engines/` is the one place a framework IS allowed - that is what the
    # package is for. `direct` is the exception inside the exception: it is the
    # baseline every other engine is measured against, and a baseline that
    # quietly acquired a graph runtime would make every comparison meaningless
    # while still passing its own conformance test.
    reached_for = sorted(_third_party_in(DIRECT_ENGINE) - ALLOWED)
    assert not reached_for, (
        f"engines/direct.py imports {reached_for}. It is the framework-free baseline; "
        "an engine that needs a library belongs beside it, not inside it."
    )


def _module_level_third_party(path: Path) -> set[str]:
    """Third-party modules a file imports AT IMPORT TIME.

    Only `tree.body`, never a full walk: an import nested inside a function is
    lazy and costs nothing until that function runs, which is the entire point
    of the distinction being tested below.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n for n in names if n not in sys.stdlib_module_names and n not in LOCAL}


def test_the_registry_does_not_hard_depend_on_a_framework():
    # `import engines` has to work wherever the agent extra is NOT installed:
    # the eval-quality CI job installs base dependencies only, and a deployment
    # serving the default `direct` engine has no reason to ship a graph runtime.
    # A module-level import in the registry would break both, and would break
    # them at import time rather than when the engine is chosen.
    hard = sorted(
        name
        for name in _module_level_third_party(ENGINE_REGISTRY_FILE)
        if name.startswith(FORBIDDEN_PREFIXES)
    )
    assert not hard, (
        f"engines/registry.py imports {hard} at module level. Import it inside the "
        "factory instead, so choosing `direct` does not require the agent extra."
    )


def test_app_never_imports_an_engine():
    # Dependencies point one way: an engine composes app/core's functions, and
    # app/ must never reach back. Asserted before `engines/` exists, because the
    # moment to state the direction is before anything can violate it.
    importers = sorted(_imports().get("engines", ()))
    assert not importers, (
        f"`app/` imports `engines/` in {importers}. The dependency runs the other "
        "way: an engine orchestrates app/core, never the reverse."
    )
