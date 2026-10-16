"""Claims with labels known by construction, for measuring the judge.

Why. `eval/grounding.py` decides the published groundedness number, and its
mistakes were found by reading the answers it flagged. A judge improved against
those same answers would be fitted to 53 claims about 53 questions. This builds
claims from passages NO suite uses, where the label follows from how the claim
was made, so a change can be designed against one split and reported on another.

Every claim is placed in a realistic context: its passage plus the nearest
chunks of OTHER articles, found with the stored vectors - the distractors
retrieval really returns.

  supported    verbatim        a sentence of the passage, as written
               composed        two non-adjacent sentences joined with "; " - the
                               shape of the composition misses in the first run
  unsupported  number_swap     one number changed to a value the context lacks
               entity_swap     one mid-sentence proper name replaced by a name
                               from an earlier passage, absent from this context
               composed_false  a composed claim whose second half is swapped -
                               the case where assembling evidence from several
                               sentences could MANUFACTURE support
               negated         "was" -> "was not"
               foreign         a sentence from an unrelated article

Labels by construction are not perfect: a swapped number could be true
somewhere, and a negation can land in a clause where it does not reverse the
claim. Swaps are checked against the whole context, and `judge_accuracy.py`
reports every kind separately so one noisy kind cannot hide in an average.

Split is by passage (`dev`/`test`) from a hash of its title, so no passage
appears on both sides, and the swapped-in names and foreign sentences of a split
come only from that split's own passages.

  uv run python scripts/build_judge_set.py --passages 60
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from build_detail_suite import eligible_title, good_passage, ids_by_title  # noqa: E402
from eval.grounding import _sentences  # noqa: E402

SEED = 20260922
OUT = BACKEND / "eval" / "judge" / "synthetic.jsonl.gz"
SUITES = ("golden", "holdout", "adversarial", "detail", "serving_golden", "serving_detail")
DISTRACTORS = 2
MIN_WORDS, MAX_WORDS = 8, 45
MAX_MARKED_SHARE = 0.4  # above this a "sentence" is a table row or a list

# A standalone number: not an ordinal ("20th"), not part of a range or a code.
_NUMBER = re.compile(r"(?<![\w.,–-])(\d{1,4})(?![\w–-]|[.,]\d)")
# A proper name inside a sentence, never its first word.
_NAME = re.compile(r"(?<=\s)([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})")
# For the pool: a name after a lower-case word or a comma, so sentence openers
# ("Thus", "Historically") never become a swapped-in entity.
_POOL_NAME = re.compile(r"(?<=[a-z,] )([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})")
_AUX = re.compile(r"\b(is|was|are|were|has|had)\b")
_ALREADY_NEGATIVE = re.compile(r"\bnot\b|n't\b|\bnever\b|\bno\b")


def suite_titles() -> set[str]:
    """Every article any suite draws on - excluded, so the sets stay disjoint."""
    titles: set[str] = set()
    for suite in SUITES:
        path = BACKEND / "eval" / f"{suite}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            titles.update(case.get("expected_titles") or [])
            if case.get("source_article"):
                titles.add(case["source_article"])
    return titles


def usable(sentence: str) -> bool:
    """Prose a model could have written: a sentence, not a list or a table row."""
    words = sentence.split()
    marked = sum(1 for word in words if word[:1].isupper() or any(c.isdigit() for c in word))
    return (MIN_WORDS <= len(words) <= MAX_WORDS
            and sentence.endswith(".")
            and marked <= len(words) * MAX_MARKED_SHARE
            and not re.match(r"(He|She|It|They|His|Her|Its|Their|This|These)\b", sentence))


def swap_number(sentence: str, context: str, rng: random.Random) -> str | None:
    """Change one number to a value that appears nowhere in the context."""
    matches = list(_NUMBER.finditer(sentence))
    rng.shuffle(matches)
    for match in matches:
        value = int(match.group(1))
        for _ in range(20):
            step = rng.randint(3, 40) if 1000 <= value <= 2100 else rng.randint(1, max(2, value))
            new = value + rng.choice([-1, 1]) * step
            if new > 0 and str(new) not in context:
                return sentence[: match.start(1)] + str(new) + sentence[match.end(1):]
    return None


def swap_entity(sentence: str, context: str, pool: list[str], rng: random.Random) -> str | None:
    """Replace one proper name with a name this context does not contain."""
    matches = list(_NAME.finditer(sentence))
    candidates = [name for name in pool if name not in context]
    if not matches or not candidates:
        return None
    match = rng.choice(matches)
    wanted = len(match.group(1).split())
    same_length = [name for name in candidates if len(name.split()) == wanted]
    return sentence[: match.start(1)] + rng.choice(same_length or candidates) + \
        sentence[match.end(1):]


def negate(sentence: str) -> str | None:
    match = _AUX.search(sentence)
    if not match or _ALREADY_NEGATIVE.search(sentence):
        return None
    return sentence[: match.end()] + " not" + sentence[match.end():]


def compose(first: str, second: str) -> str:
    return f"{first.rstrip('.')}; {second}"


def split_of(title: str) -> str:
    return "dev" if hashlib.sha1(f"{SEED}:{title}".encode()).digest()[0] % 2 == 0 else "test"


def names_in(text: str) -> list[str]:
    return sorted({match.group(1) for match in _POOL_NAME.finditer(text)
                   if len(match.group(1)) > 3})


def build(client, collection: str, passages: int, rng: random.Random) -> list[dict]:
    excluded = suite_titles()
    grouped = ids_by_title(client, collection)
    titles = sorted(t for t in grouped if eligible_title(t) and t not in excluded)
    rng.shuffle(titles)
    items: list[dict] = []
    # Swapped-in names and foreign sentences come from EARLIER passages of the
    # same split: never from this context (a name taken from a distractor is in
    # the context by definition), and never across the dev/test boundary.
    names: dict[str, list[str]] = {"dev": [], "test": []}
    foreign: dict[str, list[str]] = {"dev": [], "test": []}
    chosen = 0

    for title in titles:
        if chosen >= passages:
            break
        ids = sorted(grouped[title], key=str)
        rng.shuffle(ids)
        points = client.retrieve(collection_name=collection, ids=ids[:3],
                                 with_payload=True, with_vectors=True)
        point = next((p for p in points if good_passage((p.payload or {}).get("text", ""))), None)
        if point is None:
            continue
        text = point.payload["text"]
        sentences = _sentences(text, 1)
        good = [index for index, sentence in enumerate(sentences) if usable(sentence)]
        if len(good) < 3:
            continue
        hits = client.query_points(collection_name=collection, query=point.vector,
                                   limit=12, with_payload=True).points
        distractors = [hit.payload["text"] for hit in hits
                       if hit.payload.get("title") != title][:DISTRACTORS]
        if len(distractors) < DISTRACTORS:
            continue

        chosen += 1
        split = split_of(title)
        context = [text, *distractors]
        whole = " ".join(context)
        pool = names[split]
        base = {"title": title, "split": split,
                "context": [{"title": title, "text": text}]
                + [{"title": "", "text": d} for d in distractors]}

        def add(kind: str, label: str, claim: str | None) -> None:
            if claim:
                items.append({"id": f"syn-{len(items):04d}", "kind": kind, "label": label,
                              "claim": claim, **base})

        add("verbatim", "supported", sentences[rng.choice(good)])
        far = [(i, j) for i in good for j in good if j - i >= 2]
        if far:
            first, second = rng.choice(far)
            add("composed", "supported", compose(sentences[first], sentences[second]))
            swapped = (swap_number(sentences[second], whole, rng)
                       or swap_entity(sentences[second], whole, pool, rng))
            add("composed_false", "unsupported",
                compose(sentences[first], swapped) if swapped else None)
        order = good[:]
        rng.shuffle(order)
        add("number_swap", "unsupported",
            next((c for c in (swap_number(sentences[k], whole, rng) for k in order) if c), None))
        add("entity_swap", "unsupported",
            next((c for c in (swap_entity(sentences[k], whole, pool, rng) for k in order) if c),
                 None))
        add("negated", "unsupported",
            next((c for c in (negate(sentences[k]) for k in order) if c), None))
        if foreign[split]:
            add("foreign", "unsupported",
                foreign[split].pop(rng.randrange(len(foreign[split]))))

        foreign[split].append(sentences[rng.choice(good)])
        names[split].extend(names_in(text))
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://localhost:6333")
    parser.add_argument("--collection", default="wikipedia")
    parser.add_argument("--passages", type=int, default=60)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    from qdrant_client import QdrantClient

    client = QdrantClient(url=args.url, timeout=300)
    items = build(client, args.collection, args.passages, random.Random(SEED))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    counts: dict[str, int] = {}
    for item in items:
        key = f"{item['split']}:{item['kind']}"
        counts[key] = counts.get(key, 0) + 1
    print(f"wrote {args.out}: {len(items)} claims")
    for key in sorted(counts):
        print(f"  {key:28s} {counts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
