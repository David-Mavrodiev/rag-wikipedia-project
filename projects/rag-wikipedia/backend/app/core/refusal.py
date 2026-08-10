from __future__ import annotations

# Single source of truth for what "refused" means, shared by the API (which
# returns it verbatim on empty/low-score retrieval), the prompt (which instructs
# the model to emit it), and any client/UI that needs to tell a refusal apart
# from a real answer. Keeping it here stops those three from drifting.
REFUSAL_MESSAGE = "I don't know based on the provided context."


def is_refusal(answer: str) -> bool:
    """Return True when *answer* is a refusal, decided by the TEXT only.

    This deliberately does NOT look at whether citations were parsed. A grounded
    answer that happens to omit ``[n]`` markers (small local models do this
    intermittently) is a real answer, not a refusal — conflating "no citations"
    with "refused" is the exact bug this function exists to prevent.

    Covers both refusal kinds:
      * hard refusal — the API returns ``REFUSAL_MESSAGE`` verbatim when
        retrieval is empty or below the score threshold;
      * soft refusal — the model itself emits the refusal sentence, sometimes
        followed by a short explanation ("... The context only mentions X.").

    Tolerant to leading/trailing whitespace, wrapping quotes, case, and a curly
    apostrophe, since model output is not byte-stable.
    """
    normalized = answer.strip().strip('"').strip().lower().replace("’", "'")
    return normalized.startswith(REFUSAL_MESSAGE.lower())
