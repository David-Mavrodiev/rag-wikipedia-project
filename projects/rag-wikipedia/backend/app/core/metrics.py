"""Process-local latency sampling for the serving path.

The project measured ingestion throughput and retrieval quality in detail but
never measured what a user waits for. This closes that gap, with three
constraints that shape the whole module:

SEGMENT BY OUTCOME, OR THE NUMBER FLATTERS YOU. A hard refusal never calls the
LLM and returns in roughly 200 ms; a grounded answer waits on Ollama for
seconds. Pooled into one bucket, refusals drag the median down and the service
looks several times faster than it is for the requests anyone cares about. So
samples are keyed by (stage, outcome) and never blended. A single p50 across
outcomes would be a self-flattering metric, which is precisely the failure this
project exists to catch elsewhere.

SAY WHAT THE NUMBER IS. A percentile from one process's ring buffer is not a
fleet's percentile, and a p95 from twenty samples is noise. `snapshot()`
therefore reports the window size, the sample count, the percentile method, and
`process_local: true`, and returns None for a percentile it does not have the
samples to support - the same discipline `audit_report.json` applies to quality
numbers.

RECORD FAILURES. Timing is taken in a finally block by the caller, so a 503
still produces a sample. A failed request's latency is more interesting than a
successful one's, not less.

Deliberately NOT prometheus-client. Its histograms and scrape format are the
right answer the moment there is more than one replica to aggregate across;
against a single local process they buy a text encoding and a dependency. The
migration trigger is a second replica, and `snapshot()` is shaped to map onto
histogram buckets when that day comes.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime

# Samples retained per (stage, outcome). At ~3 s per answered query this is
# hours of traffic on a demo box, and the whole structure is a few hundred KB.
DEFAULT_WINDOW = 512

# Below this many samples a p95 is dominated by whichever request happened to be
# slowest, so it is reported as None rather than as a number that looks precise.
# p50 survives a much smaller sample, hence the separate floor.
MIN_SAMPLES_P95 = 100
MIN_SAMPLES_P50 = 5

# The stages worth separating. `total` is what the user feels; the rest exist to
# answer "which part?" - without them a slow query is just slow.
#
# These do NOT sum to `total`, deliberately. `retrieve` contains `embed` and
# `search` plus reranking, evidence scoring and token budgeting; the remainder
# of `total` is prompt building and serialisation. Reporting a decomposition
# that claims to be exhaustive when it is not would be its own small lie, so
# the nesting is documented instead of hidden.
# `deps` is cold-start: constructing the embedder and vector-store clients on
# the first request after a restart. It is near-zero on every subsequent
# request, so a high max with a ~0 p50 is the expected shape, and is the signal
# that dependencies are built lazily on the request path rather than at boot.
STAGES = ("deps", "embed", "search", "retrieve", "generate", "total")

# `error` covers 5xx from a dependency. `refused` is a correct, fast answer, not
# a failure - keeping it out of `ok` is the entire point of the segmentation.
OUTCOMES = ("ok", "refused", "error")


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an already-sorted list.

    Nearest-rank rather than linear interpolation: it always returns a value
    that was actually observed, which makes a reported p95 checkable against the
    raw samples. The two definitions disagree, so `snapshot()` names the one in
    use rather than leaving a reader to assume.

    Caller guarantees the list is sorted and non-empty.
    """
    rank = max(1, -(-len(sorted_values) * q // 100))  # ceil, integer-only
    return sorted_values[int(rank) - 1]


class LatencyRecorder:
    """Bounded, thread-safe latency samples keyed by (stage, outcome)."""

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self._window = window
        self._lock = threading.Lock()
        self._samples: dict[tuple[str, str], deque[float]] = {}
        self._started_at = datetime.now(UTC)

    def record(self, stage: str, outcome: str, ms: float) -> None:
        """Add one sample. Unknown stages/outcomes are dropped, not raised.

        This runs inside a request's finally block. A typo in a stage name must
        not turn a served response into a 500 - the metric is not worth the
        request.
        """
        if stage not in STAGES or outcome not in OUTCOMES:
            return
        with self._lock:
            bucket = self._samples.get((stage, outcome))
            if bucket is None:
                bucket = self._samples[(stage, outcome)] = deque(maxlen=self._window)
            bucket.append(ms)

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()

    def snapshot(self) -> dict:
        """Percentiles per stage and outcome, with the caveats attached."""
        with self._lock:
            # Copy under the lock, compute outside it: percentile work should
            # not block recording.
            raw = {key: list(values) for key, values in self._samples.items()}
            started_at = self._started_at

        stages: dict[str, dict[str, dict]] = {}
        counts: dict[str, int] = dict.fromkeys(OUTCOMES, 0)

        for (stage, outcome), values in raw.items():
            if not values:
                continue
            if stage == "total":
                # Count each request once; per-stage buckets would multiply it.
                counts[outcome] = len(values)
            ordered = sorted(values)
            entry: dict = {
                "samples": len(ordered),
                "min_ms": round(ordered[0], 1),
                "max_ms": round(ordered[-1], 1),
                "p50_ms": (
                    round(percentile(ordered, 50), 1)
                    if len(ordered) >= MIN_SAMPLES_P50
                    else None
                ),
                "p95_ms": (
                    round(percentile(ordered, 95), 1)
                    if len(ordered) >= MIN_SAMPLES_P95
                    else None
                ),
            }
            notes = []
            if entry["p50_ms"] is None:
                notes.append(f"insufficient samples for p50 ({len(ordered)} < {MIN_SAMPLES_P50})")
            if entry["p95_ms"] is None:
                notes.append(f"insufficient samples for p95 ({len(ordered)} < {MIN_SAMPLES_P95})")
            if notes:
                entry["note"] = "; ".join(notes)
            stages.setdefault(stage, {})[outcome] = entry

        return {
            # Provenance, for the same reason audit_report.json carries it: a
            # number published without its scope invites being read as one it
            # does not have.
            "process_local": True,
            "process_started_at": started_at.isoformat(),
            "window_per_series": self._window,
            "percentile_method": "nearest-rank",
            "outcome_counts": counts,
            "stages": stages,
        }


recorder = LatencyRecorder()
