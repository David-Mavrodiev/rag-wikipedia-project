"""What an engine is, and what every engine must return.

An ENGINE is the orchestration layer: it decides which steps in `app.core` to
call, and in what order. `direct` calls them once, in a line. A graph runtime
can grade the evidence, rewrite the question and retry, then verify the answer
before returning it. Both are engines, and both answer the same question the
same way as far as the caller can tell.

The interface exists so that choosing between them is one value rather than a
rewrite - and, more importantly, so the two can be MEASURED against each other
on the same suites. A framework that cannot be compared against not using it is
a framework nobody can justify.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EngineResult:
    """One answered question.

    The first three fields mean exactly what `QueryResponse` means by them, so
    an engine can back the API without translation.

    `refusal_reason` and `stats` are additions the HTTP response does not
    carry. They exist for comparison: an engine that refuses more often is only
    better if it refuses for the right reason, and an engine that scores higher
    at four times the LLM calls has a price that belongs beside its score.
    """

    answer: str
    citations: list[dict] = field(default_factory=list)
    refused: bool = False
    # None when answered. Coarse on purpose - see DirectEngine.answer for why
    # the precise evidence reason is not available to a caller of retrieve().
    refusal_reason: str | None = None
    stats: dict[str, int] = field(default_factory=dict)


class Engine(ABC):
    """One question in, one grounded-or-refused answer out.

    CONTRACT - an engine ORCHESTRATES `app.core`, it never reimplements it.
    Chunking, retrieval, the evidence gate, the prompt contract, refusal
    detection and citation building stay where they are, unit-tested, with one
    definition each. An engine chooses what to call and when.

    That line is what keeps a comparison meaningful. The moment an engine
    decides for itself what counts as a refusal, two engines stop being two
    strategies over one system and become two systems, and the eval suites no
    longer describe either of them.

    CONTRACT - engines do not translate errors. A vector store that is down
    raises out of `answer()`. Mapping that to a 503 is the job of the API, and
    the eval harness wants the exception rather than a status code.
    """

    @abstractmethod
    def answer(self, question: str) -> EngineResult:
        raise NotImplementedError
