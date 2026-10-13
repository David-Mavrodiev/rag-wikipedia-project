"""Ship the corpus as a file instead of re-ingesting it in the cloud.

Ingestion is hours of embedding; a Qdrant snapshot of the same collection is
**328 MB measured** for 87,173 chunks and restores in about a minute. So the
corpus is built once, on a machine that can afford it, and every host after that
receives the file.

    python scripts/corpus_snapshot.py create                 # on the machine that has the corpus
    python scripts/corpus_snapshot.py download --out c.snap  # fetch what `create` made
    python scripts/corpus_snapshot.py restore --from-url URL --expect 87173
    python scripts/corpus_snapshot.py verify --expect 87173  # what a boot check runs

`restore` and `verify` refuse a collection that is not the one the published
reports were measured against: a demo answering from a different corpus than the
numbers describe is worse than a demo that does not start.

Standard library plus qdrant_client, so it runs inside the serving image.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
DEFAULT_COLLECTION = os.environ.get("COLLECTION", "wikipedia")
POLL_S = 2.0


def _client(url: str):
    from qdrant_client import QdrantClient

    return QdrantClient(url=url, timeout=600)


def _api(url: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{url.rstrip('/')}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read())


def create(url: str, collection: str) -> int:
    result = _api(url, f"/collections/{collection}/snapshots", method="POST")["result"]
    size_mb = result.get("size", 0) / 1e6
    print(f"created {result['name']} ({size_mb:.1f} MB)")
    print(f"download it from {url.rstrip('/')}/collections/{collection}/snapshots/{result['name']}")
    return 0


def download(url: str, collection: str, out: Path, name: str | None = None) -> int:
    if name is None:
        snapshots = _api(url, f"/collections/{collection}/snapshots")["result"]
        if not snapshots:
            print("no snapshot on that server - run `create` first", file=sys.stderr)
            return 2
        name = sorted(snapshots, key=lambda s: s["creation_time"])[-1]["name"]
    source = f"{url.rstrip('/')}/collections/{collection}/snapshots/{name}"
    out.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(source, timeout=900) as response, out.open("wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


def restore(url: str, collection: str, from_url: str, expect: int) -> int:
    """Ask Qdrant to pull the snapshot itself: the file never passes through here."""
    print(f"restoring {collection} from {from_url}")
    _api(url, f"/collections/{collection}/snapshots/recover", method="PUT",
         payload={"location": from_url, "priority": "snapshot"})
    for _ in range(120):
        try:
            if verify(url, collection, expect, quiet=True) == 0:
                break
        except Exception:  # noqa: BLE001 - the collection may not exist yet
            pass
        time.sleep(POLL_S)
    return verify(url, collection, expect)


def verify(url: str, collection: str, expect: int, *, quiet: bool = False) -> int:
    """The boot check: the right collection, the right size, and it answers."""
    info = _api(url, f"/collections/{collection}")["result"]
    points = info.get("points_count") or 0
    status = info.get("status")
    vectors = (info.get("config", {}).get("params", {}).get("vectors") or {})
    problems = []
    if points != expect:
        problems.append(f"{points} points, expected {expect}")
    if status != "green":
        problems.append(f"status {status}")
    if vectors.get("size") not in (None, 384):
        problems.append(f"vector size {vectors.get('size')}, expected 384")
    if problems:
        if not quiet:
            print(f"corpus check FAILED for `{collection}`: {'; '.join(problems)}", file=sys.stderr)
        return 1
    if not quiet:
        print(f"corpus OK: `{collection}` {points} points, status {status}, "
              f"{vectors.get('size')}-d vectors")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["create", "download", "restore", "verify"])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--out", type=Path, default=Path("corpus.snapshot"))
    parser.add_argument("--name", default=None, help="snapshot name; default is the newest")
    parser.add_argument("--from-url", default=os.environ.get("SNAPSHOT_URL", ""))
    parser.add_argument("--expect", type=int,
                        default=int(os.environ.get("EXPECTED_POINTS", "87173")))
    args = parser.parse_args(argv)

    if args.command == "create":
        return create(args.url, args.collection)
    if args.command == "download":
        return download(args.url, args.collection, args.out, args.name)
    if args.command == "restore":
        if not args.from_url:
            print("restore needs --from-url (or SNAPSHOT_URL)", file=sys.stderr)
            return 2
        return restore(args.url, args.collection, args.from_url, args.expect)
    return verify(args.url, args.collection, args.expect)


if __name__ == "__main__":
    raise SystemExit(main())
