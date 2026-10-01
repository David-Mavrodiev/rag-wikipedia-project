"""A daily check on Modal's REAL bill, not on our estimate of it.

    modal billing report --for today --json > today.json
    python infra/modal/watchdog.py today.json      # exit 2 = over the ceiling

Two limits, on purpose, and they measure different things:

* **The pacer**, in the API, spends against our own estimate of what a request
  costs, and can refuse to send work to the GPU.
* **This**, in CI, reads what Modal actually charged. A gap between the two is a
  bug - a container that never scaled down, a loop, a price that changed - and
  it is visible every morning instead of at the end of the month.

Over the ceiling the workflow stops the app. The service does not go down: the
API falls back to Oracle's CPU with a visible notice, which is the whole reason
there are two hosts.

Standard library only: it runs on a bare CI runner.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

APPS = ("rag-wikipedia-inference", "rag-wikipedia-worker")
DEFAULT_CEILING_USD = Decimal("1.00")


@dataclass(frozen=True)
class Verdict:
    total_usd: Decimal
    apps_usd: Decimal
    ceiling_usd: Decimal

    @property
    def stop(self) -> bool:
        return self.total_usd > self.ceiling_usd

    def markdown(self) -> str:
        state = ("🛑 over the ceiling: stopping the apps" if self.stop
                 else "✅ within the ceiling")
        return (
            "### Modal watchdog\n\n"
            "| today (UTC) | value |\n|---|---:|\n"
            f"| workspace cost | ${self.total_usd:.4f} |\n"
            f"| of which this project | ${self.apps_usd:.4f} |\n"
            f"| daily ceiling | ${self.ceiling_usd} |\n\n"
            f"{state}\n"
        )


def judge(rows: list[dict], ceiling_usd: Decimal = DEFAULT_CEILING_USD) -> Verdict:
    total = sum((Decimal(str(row["cost"])) for row in rows), Decimal(0))
    ours = sum(
        (Decimal(str(row["cost"])) for row in rows if row.get("description") in APPS),
        Decimal(0),
    )
    return Verdict(total, ours, ceiling_usd)


def main(argv: list[str]) -> int:
    raw = Path(argv[1]).read_text(encoding="utf-8") if len(argv) > 1 else sys.stdin.read()
    ceiling = Decimal(os.environ.get("MODAL_DAILY_CEILING_USD") or DEFAULT_CEILING_USD)
    verdict = judge(json.loads(raw), ceiling)
    sys.stdout.reconfigure(encoding="utf-8")
    print(verdict.markdown())
    return 2 if verdict.stop else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
