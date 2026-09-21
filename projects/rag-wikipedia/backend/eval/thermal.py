"""GPU conditions for a measurement, and pacing that keeps a laptop GPU safe.

Why this exists. Measured 2026-09-21 on this project's reference machine (RTX
3060 Laptop GPU, 6 GB): under a continuous end-to-end run the SM clock pins at
210 MHz of 2,100 within minutes while the card holds 90-92 C, and a guard had to
stop the run at 96 C after 33 generations. The same generation takes ~4 s on a
cool card and ~20 s on a clamped one. A latency figure that does not say which
of those it was taken under is how this repository came to publish both "3 s"
(the demo) and "48 s" (engine_comparison.md) for the same system.

Two tools, both optional at run time:

* `GpuSampler` polls `nvidia-smi` once a second in a background thread and
  summarises any time window: the median SM clock while the GPU was busy, the
  peak temperature, and a regime label. Every timed call gets its conditions
  attached, so a number carries the circumstances it was measured in.
* `ThermalPacer` runs BEFORE each generation. Above `pause_at` it waits until
  the card is back down to `resume_at`. It changes when the next call starts,
  never what that call measures.

The regime is read from the CLOCK, not from nvidia-smi's throttle-reason flags.
On this driver `sw_thermal_slowdown` already reports Active at 75 C and
1,425 MHz, so the flag cannot separate a cool card from a clamped one; the clock
separates them by a factor of seven.

Without nvidia-smi (CI, a CPU-only machine, another vendor's GPU) every reading
is None and pacing is a no-op. Reports then say "unavailable" instead of
inventing conditions.
"""

from __future__ import annotations

import logging
import shutil
import statistics
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

QUERY_FIELDS = ("clocks.sm", "clocks.max.sm", "temperature.gpu", "utilization.gpu")

# A sample counts as "busy" at or above this utilization. Idle clocks are low by
# design (power saving), so an idle reading says nothing about throttling.
BUSY_UTILIZATION_PCT = 50

# Median busy clock at or above this fraction of the rated maximum reads as
# "full". The two regimes observed on the reference machine are 1,425 and
# 210 MHz of 2,100 (0.68 and 0.10), so the boundary is not a fine judgement.
FULL_CLOCK_FRACTION = 0.5


@dataclass(frozen=True)
class GpuReading:
    at: float  # time.monotonic() when the line was read
    sm_mhz: int
    max_sm_mhz: int
    temp_c: int
    util_pct: int


def parse_line(line: str, at: float) -> GpuReading | None:
    """One CSV line from nvidia-smi, or None for anything unparseable.

    nvidia-smi prints "[N/A]" or "[Not Supported]" for fields a GPU does not
    expose. A partial reading is dropped rather than half-trusted.
    """
    parts = [part.strip() for part in line.split(",")]
    if len(parts) != len(QUERY_FIELDS):
        return None
    try:
        sm, max_sm, temp, util = (int(float(part)) for part in parts)
    except ValueError:
        return None
    return GpuReading(at=at, sm_mhz=sm, max_sm_mhz=max_sm, temp_c=temp, util_pct=util)


def _command() -> list[str]:
    return [
        "nvidia-smi",
        "--id=0",
        f"--query-gpu={','.join(QUERY_FIELDS)}",
        "--format=csv,noheader,nounits",
    ]


def available() -> bool:
    return shutil.which("nvidia-smi") is not None


def read_gpu(run: Callable = subprocess.run) -> GpuReading | None:
    """A single reading, or None when there is no NVIDIA GPU to read."""
    if not available():
        return None
    try:
        completed = run(_command(), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    lines = completed.stdout.strip().splitlines()
    return parse_line(lines[0], time.monotonic()) if lines else None


def gpu_name(run: Callable = subprocess.run) -> str | None:
    if not available():
        return None
    try:
        completed = run(
            ["nvidia-smi", "--id=0", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = completed.stdout.strip()
    return name or None


def regime(sm_mhz: float, max_sm_mhz: int) -> str:
    if max_sm_mhz <= 0:
        return "unknown"
    return "full" if sm_mhz >= FULL_CLOCK_FRACTION * max_sm_mhz else "throttled"


def summarize(readings: list[GpuReading]) -> dict | None:
    """Conditions over a window of readings.

    The clock is the median over BUSY samples only. A window with no busy sample
    (a call shorter than the sampling interval, or one the GPU never worked on)
    reports regime "unknown" rather than classifying an idle clock.
    """
    if not readings:
        return None
    busy = [reading for reading in readings if reading.util_pct >= BUSY_UTILIZATION_PCT]
    max_sm = readings[-1].max_sm_mhz
    summary = {
        "samples": len(readings),
        "busy_samples": len(busy),
        "peak_temp_c": max(reading.temp_c for reading in readings),
        "max_sm_mhz": max_sm,
    }
    if not busy:
        return {**summary, "sm_mhz": None, "regime": "unknown"}
    clock = statistics.median(reading.sm_mhz for reading in busy)
    return {**summary, "sm_mhz": int(clock), "regime": regime(clock, max_sm)}


class GpuSampler:
    """Polls the GPU from a background thread; summarises any time window.

    A poll per interval, not `nvidia-smi --loop-ms` streaming. The streaming
    form was tried first and does not work here: with stdout on a pipe,
    nvidia-smi block-buffers its output, and a 10-second test delivered ONE line.
    Lines arriving in bursts would also carry the wrong timestamps. A single
    reading costs ~50 ms once warm (measured), off the timed path in its own
    thread.
    """

    def __init__(
        self,
        interval_s: float = 1.0,
        keep: int = 21_600,
        read: Callable[[], GpuReading | None] = read_gpu,
    ) -> None:
        self._interval_s = interval_s
        self._readings: deque[GpuReading] = deque(maxlen=keep)
        self._lock = threading.Lock()
        self._read = read
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Begin sampling. False when there is nothing to sample."""
        if self._read() is None:
            return False
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return True

    def _pump(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            reading = self._read()
            if reading is not None:
                with self._lock:
                    self._readings.append(reading)
            # Subtract the read's own duration so the PERIOD is the interval.
            self._stop.wait(max(0.0, self._interval_s - (time.monotonic() - started)))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)

    def __enter__(self) -> GpuSampler:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def latest(self) -> GpuReading | None:
        with self._lock:
            return self._readings[-1] if self._readings else None

    def window(self, start: float, end: float) -> list[GpuReading]:
        with self._lock:
            return [reading for reading in self._readings if start <= reading.at <= end]

    def summary(self, start: float, end: float) -> dict | None:
        return summarize(self.window(start, end))


class ThermalAbort(RuntimeError):
    """The GPU did not cool within the allowed wait. Stop, do not push through."""


class ThermalPacer:
    """Wait before a generation while the GPU is hot.

    Hysteresis on purpose: resuming the moment the card dips under `pause_at`
    would trade one long cool-down for a stutter of short ones, each ending at
    the edge of the limit.
    """

    def __init__(
        self,
        pause_at: int = 85,
        resume_at: int = 75,
        *,
        max_wait_s: float = 900.0,
        poll_s: float = 5.0,
        read: Callable[[], GpuReading | None] = read_gpu,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if resume_at >= pause_at:
            raise ValueError(f"resume_at ({resume_at}) must be below pause_at ({pause_at})")
        self.pause_at = pause_at
        self.resume_at = resume_at
        self._max_wait_s = max_wait_s
        self._poll_s = poll_s
        self._read = read
        self._sleep = sleep
        self._clock = clock
        self.total_wait_s = 0.0
        self.pauses = 0

    def before_call(self) -> float:
        """Block until it is safe to generate. Returns the seconds waited."""
        reading = self._read()
        if reading is None or reading.temp_c < self.pause_at:
            return 0.0

        self.pauses += 1
        started = self._clock()
        logger.info(
            "GPU at %s C (pause at %s): cooling to %s C before the next call",
            reading.temp_c,
            self.pause_at,
            self.resume_at,
        )
        while True:
            self._sleep(self._poll_s)
            reading = self._read()
            waited = self._clock() - started
            if reading is None or reading.temp_c <= self.resume_at:
                break
            if waited > self._max_wait_s:
                raise ThermalAbort(
                    f"GPU still at {reading.temp_c} C after {waited:.0f} s of cooling "
                    f"(resume at {self.resume_at} C). Stopping rather than pushing on."
                )
        self.total_wait_s += waited
        return waited
