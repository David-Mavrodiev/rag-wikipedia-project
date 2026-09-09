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


def test_app_never_imports_an_engine():
    # Dependencies point one way: an engine composes app/core's functions, and
    # app/ must never reach back. Asserted before `engines/` exists, because the
    # moment to state the direction is before anything can violate it.
    importers = sorted(_imports().get("engines", ()))
    assert not importers, (
        f"`app/` imports `engines/` in {importers}. The dependency runs the other "
        "way: an engine orchestrates app/core, never the reverse."
    )
