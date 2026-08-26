from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

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
