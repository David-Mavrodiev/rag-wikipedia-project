from __future__ import annotations

import gzip
import json
import logging
import random
import time
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

# A 25k run streams for well over an hour. Transient upstream failures are not
# hypothetical: a HuggingFace CDN 503 killed a run 90 seconds in, and the same
# machine also saw a DNS failure and a truncated read on the same day.
MAX_STREAM_RESTARTS = 6

# The committed evaluation corpus. Small, fixed, and in version control so the
# audit measures the same thing on every machine - see
# scripts/build_eval_fixture.py for why it exists and how it is sized.
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "eval" / "fixtures" / "corpus_eval.jsonl.gz"

PROFILE_CONFIG = {
    # No network, no Hugging Face, deterministic: this is what CI ingests and
    # what the published audit_report.json is measured against.
    "fixture": {"source": "file", "n_articles": None},
    "tiny": {"split": "train", "streaming": True, "n_articles": 500},
    "real": {"split": "train", "streaming": True, "n_articles": 25_000},
}


def _iter_fixture() -> Iterator[dict]:
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(
            f"{FIXTURE_PATH} is missing. Rebuild it with "
            "`uv run python scripts/build_eval_fixture.py`."
        )
    with gzip.open(FIXTURE_PATH, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def iter_articles(profile: str = "tiny") -> Iterator[dict]:
    """Yield dicts with keys: id, title, text."""
    if profile == "fixture":
        logger.info("Loading the committed evaluation corpus from %s", FIXTURE_PATH)
        yield from _iter_fixture()
        return

    from datasets import load_dataset

    cfg = PROFILE_CONFIG.get(profile, PROFILE_CONFIG["tiny"])
    n_articles = cfg["n_articles"]
    logger.info("Loading %s articles from wikimedia/wikipedia (%s)", n_articles, profile)
    dataset = load_dataset(
        "wikimedia/wikipedia",
        "20231101.en",
        split=cfg["split"],
        streaming=True,
        trust_remote_code=True,
    )
    for index, row in enumerate(dataset):
        if index >= n_articles:
            break
        yield {"id": row["id"], "title": row["title"], "text": row["text"]}


def _is_transient(exc: BaseException) -> bool:
    """Should this failure be retried?

    5xx, 429 and dropped connections heal on their own. 401/403/404 do not, and
    retrying them just delays a clear error behind six backoffs.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status == 429 or 500 <= int(status) < 600
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    # requests/urllib3 wrap dropped reads in their own hierarchy; match by name
    # rather than importing them here.
    return exc.__class__.__name__ in {
        "ChunkedEncodingError",
        "ConnectionError",
        "ConnectTimeout",
        "IncompleteRead",
        "ProtocolError",
        "ReadTimeout",
        "ReadTimeoutError",
    }


def iter_articles_resilient(
    profile: str = "tiny", *, max_restarts: int = MAX_STREAM_RESTARTS
) -> Iterator[dict]:
    """Yield articles, surviving transient failures of the upstream stream.

    A generator cannot resume after it raises, so a retry restarts the stream and
    discards what this run already delivered. Two deliberate choices:

    * The counter lives in memory and describes only this process. Nothing is
      skipped on the strength of a stored index, so a stale file can never cause
      articles to be silently missed - the only skipping that reaches Qdrant is
      the existence check, which verifies against the collection.
    * The stream is replayed rather than seeked. datasets' skip() is SLOWER than
      re-reading here (measured: skip(1000) 46.2s vs iterate 1000 18.7s), so
      seeking would cost time and buy nothing.

    The restart budget resets whenever progress is made, so six failures spread
    across a long run are survivable; only six consecutive ones are fatal.
    """
    delivered = 0
    restarts = 0
    while True:
        try:
            seen = 0
            for article in iter_articles(profile):
                seen += 1
                if seen <= delivered:
                    continue  # replayed after a restart - already handed out
                delivered = seen
                restarts = 0  # progress earns the budget back
                yield article
            return
        except Exception as exc:  # noqa: BLE001 - re-raised unless transient
            if not _is_transient(exc) or restarts >= max_restarts:
                raise
            restarts += 1
            wait = min(60.0, 2.0**restarts) + random.uniform(0, 1)
            logger.warning(
                "Stream failed after %d articles (%s: %s). Restart %d/%d in %.1fs.",
                delivered,
                exc.__class__.__name__,
                str(exc)[:120],
                restarts,
                max_restarts,
                wait,
            )
            time.sleep(wait)
