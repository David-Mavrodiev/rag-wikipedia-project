from __future__ import annotations

# Single source of truth for what "refused" means, shared by the API (which
# returns it verbatim on empty/low-score retrieval), the prompt (which instructs
# the model to emit it), and any client/UI that needs to tell a refusal apart
# from a real answer. Keeping it here stops those three from drifting.
REFUSAL_MESSAGE = "I don't know based on the provided context."

# Smart (curly) quotes -> ASCII. Model output is not byte-stable: the same
# refusal can come back wrapped in curly double quotes or written with a curly
# apostrophe, and none of that should change the verdict. Handling only the
# ASCII forms is how a refusal silently gets reported as a grounded answer.
_SMART_QUOTES = str.maketrans(
    {
        "“": '"',  # left double quotation mark
        "”": '"',  # right double quotation mark
        "‘": "'",  # left single quotation mark
        "’": "'",  # right single quotation mark (curly apostrophe)
    }
)


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

    Tolerant to leading/trailing whitespace, wrapping quotes (ASCII *and*
    curly), case, and curly apostrophes, since model output is not byte-stable.
    """
    # Normalize smart quotes BEFORE stripping, so curly-wrapped answers get
    # their quotes removed too; then strip again for any inner whitespace.
    normalized = answer.strip().translate(_SMART_QUOTES).strip('"').strip().lower()
    return normalized.startswith(REFUSAL_MESSAGE.lower())
