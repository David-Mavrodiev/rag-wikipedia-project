"""Build the committed evaluation corpus.

The audit's numbers are only comparable between runs if the corpus is identical
between runs. Measuring against "whatever is in a local Qdrant" produced exactly
the problem this fixes: the same code and suites scored golden precision 0.940
on a 60-article corpus and 0.890 on a 500-article one, with nothing recording
which.

So the published measurement is taken against a fixed corpus committed to the
repository. Anyone - CI, a reviewer, a future you - can reproduce it exactly.

Sizing is set by what CI can afford to embed, not by fidelity to the full
profile. Embedding is ~97% of ingestion time; the full `tiny` profile is ~6,400
chunks and 15-30 minutes on a CI runner, which is far too slow for a per-PR job.
ARTICLE_COUNT is the smallest number that still contains every title the eval
suites reference (all 55 appear within the first 60 articles) while leaving a
meaningful set of distractors - without distractors the out-of-corpus refusal
cases would be trivially easy.

Regenerate only when the suites start referencing a title outside the current
window; the fixture is otherwise meant to be stable, because changing it
invalidates every recorded measurement.

Usage:
    uv run python scripts/build_eval_fixture.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

FIXTURE = BACKEND / "eval" / "fixtures" / "corpus_eval.jsonl.gz"
ARTICLE_COUNT = 150


def required_titles() -> set[str]:
    titles: set[str] = set()
    for name in ("golden", "holdout", "adversarial"):
        path = BACKEND / "eval" / f"{name}.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                titles.update(json.loads(line).get("expected_titles") or [])
    return titles


def main() -> int:
    from pipeline.sources import iter_articles

    needed = required_titles()
    articles = []
    for index, article in enumerate(iter_articles("tiny")):
        if index >= ARTICLE_COUNT:
            break
        articles.append(article)

    present = {a["title"] for a in articles}
    missing = sorted(needed - present)
    if missing:
        # Refuse to write a fixture the suites cannot be scored against: every
        # expected_titles entry would silently count as a miss.
        print(
            f"REFUSING to write the fixture: {len(missing)} title(s) referenced by "
            f"the eval suites are outside the first {ARTICLE_COUNT} articles:\n  "
            + "\n  ".join(missing[:10])
            + "\n\nRaise ARTICLE_COUNT or adjust the suites."
        )
        return 1

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(a, ensure_ascii=False) + "\n" for a in articles)
    # mtime=0: gzip embeds a timestamp, which would make the committed file
    # differ on every rebuild and produce noisy diffs for identical content.
    with gzip.GzipFile(FIXTURE, "wb", compresslevel=9, mtime=0) as handle:
        handle.write(payload.encode("utf-8"))

    size_mb = FIXTURE.stat().st_size / 1e6
    print(
        f"wrote {FIXTURE.relative_to(BACKEND.parent)}\n"
        f"  articles         : {len(articles)}\n"
        f"  distinct titles  : {len(present)}\n"
        f"  required present : {len(needed)}/{len(needed)}\n"
        f"  distractors      : {len(present - needed)}\n"
        f"  size on disk     : {size_mb:.2f} MB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
