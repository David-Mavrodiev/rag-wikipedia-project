from __future__ import annotations

import logging
from collections.abc import Iterator

logger = logging.getLogger(__name__)

PROFILE_CONFIG = {
    "tiny": {"split": "train", "streaming": True, "n_articles": 500},
    "real": {"split": "train", "streaming": True, "n_articles": 25_000},
}


def iter_articles(profile: str = "tiny") -> Iterator[dict]:
    """Yield dicts with keys: id, title, text."""
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
