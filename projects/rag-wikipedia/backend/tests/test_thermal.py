"""GPU conditions and pacing: the parts a latency number's honesty depends on.

Everything here runs without a GPU. Readings, clocks and sleeps are injected,
so CI exercises the same decisions the reference laptop makes under load.
"""

from __future__ import annotations

import subprocess
import time

import pytest
from eval import thermal
from eval.thermal import (
    GpuReading,
    GpuSampler,
    ThermalAbort,
    ThermalPacer,
    parse_line,
    regime,
    summarize,
)


def _reading(temp: int = 60, sm: int = 1425, util: int = 100, at: float = 0.0) -> GpuReading:
    return GpuReading(at=at, sm_mhz=sm, max_sm_mhz=2100, temp_c=temp, util_pct=util)


# --- parsing ------------------------------------------------------------------

def test_a_csv_line_parses_into_a_reading():
    assert parse_line("1425, 2100, 77, 98", at=1.5) == GpuReading(1.5, 1425, 2100, 77, 98)


@pytest.mark.parametrize("line", ["[N/A], 2100, 77, 98", "1425, 2100, 77", ""])
def test_an_unreadable_line_is_dropped_not_half_trusted(line):
    assert parse_line(line, at=0.0) is None


def test_no_nvidia_smi_means_no_reading(monkeypatch):
    monkeypatch.setattr(thermal, "available", lambda: False)
    assert thermal.read_gpu() is None


def test_a_failing_nvidia_smi_means_no_reading(monkeypatch):
    monkeypatch.setattr(thermal, "available", lambda: True)

    def failing_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=9, stdout="", stderr="no device")

    assert thermal.read_gpu(run=failing_run) is None


# --- regime -------------------------------------------------------------------

def test_the_two_observed_regimes_classify_apart():
    # 1,425 and 210 MHz of 2,100: the cool and the clamped card, measured.
    assert regime(1425, 2100) == "full"
    assert regime(210, 2100) == "throttled"


def test_the_clock_is_the_median_of_busy_samples_only():
    # An idle clock is low by design; it must not drag a busy window down.
    readings = [
        _reading(sm=210, util=0),
        _reading(sm=1425, util=99),
        _reading(sm=1400, util=97),
        _reading(sm=1450, util=100),
    ]
    summary = summarize(readings)
    assert summary["sm_mhz"] == 1425
    assert summary["busy_samples"] == 3
    assert summary["regime"] == "full"


def test_a_window_with_no_busy_sample_is_unknown_not_classified():
    summary = summarize([_reading(sm=210, util=0), _reading(sm=210, util=3)])
    assert summary["regime"] == "unknown"
    assert summary["sm_mhz"] is None


def test_peak_temperature_covers_every_sample():
    summary = summarize([_reading(temp=70, util=0), _reading(temp=91, util=100)])
    assert summary["peak_temp_c"] == 91


def test_an_empty_window_has_no_summary():
    assert summarize([]) is None


# --- pacing -------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _pacer(temps: list[int | None], **kwargs):
    readings = iter(temps)
    clock = _Clock()

    def read():
        temp = next(readings)
        return None if temp is None else _reading(temp=temp)

    pacer = ThermalPacer(read=read, sleep=clock.sleep, clock=clock, poll_s=5, **kwargs)
    return pacer, clock


def test_a_cool_gpu_is_not_paced():
    pacer, clock = _pacer([60])
    assert pacer.before_call() == 0.0
    assert clock.now == 0.0


def test_a_hot_gpu_waits_until_the_resume_temperature():
    pacer, clock = _pacer([88, 84, 80, 74], pause_at=85, resume_at=75)
    waited = pacer.before_call()
    assert waited == 15.0
    assert pacer.pauses == 1


def test_resuming_needs_the_lower_threshold_not_just_below_the_upper():
    # Hysteresis: 84 is under pause_at but above resume_at, so it keeps waiting.
    pacer, clock = _pacer([86, 84, 84, 75], pause_at=85, resume_at=75)
    assert pacer.before_call() == 15.0


def test_no_gpu_means_no_pacing():
    pacer, clock = _pacer([None])
    assert pacer.before_call() == 0.0


def test_a_gpu_that_will_not_cool_stops_the_run():
    pacer, _ = _pacer([95] * 10, pause_at=85, resume_at=75, max_wait_s=12)
    with pytest.raises(ThermalAbort):
        pacer.before_call()


def test_resume_must_be_below_pause():
    with pytest.raises(ValueError):
        ThermalPacer(pause_at=80, resume_at=80)


# --- sampler ------------------------------------------------------------------

def test_the_sampler_reports_unavailable_without_a_gpu():
    assert GpuSampler(read=lambda: None).start() is False


def test_the_sampler_collects_and_windows_readings():
    # No assertion depends on two readings having distinct timestamps: on
    # Windows time.monotonic() ticks every 15.6 ms, so two polls can share one.
    sampler = GpuSampler(interval_s=0.02, read=lambda: _reading(at=time.monotonic()))
    assert sampler.start() is True
    time.sleep(0.15)
    sampler.stop()
    readings = sampler.window(0.0, time.monotonic())
    assert len(readings) >= 2
    first, last = readings[0].at, readings[-1].at
    assert len(sampler.window(first, last)) == len(readings)
    assert sampler.window(last + 1.0, last + 2.0) == []
    assert sampler.window(0.0, first - 1.0) == []
