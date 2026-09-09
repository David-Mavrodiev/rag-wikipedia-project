"""The stream must survive transient upstream failures without skipping anything.

A HuggingFace CDN 503 killed a 25k run 90 seconds in. Over a two-hour stream
that is not an edge case, and the same machine saw a DNS failure and a truncated
read on the same day.
"""

import pytest
from pipeline import sources


class _Boom(Exception):
    """Stands in for a transient upstream error."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()


def _articles(n):
    return [{"id": str(i), "title": f"T{i}", "text": "x"} for i in range(n)]


def _flaky_source(fail_at, exc, monkeypatch, total=10):
    """A stream that raises once, partway through, then succeeds on replay."""
    state = {"raised": False}

    def fake(profile):
        for index, article in enumerate(_articles(total)):
            if index == fail_at and not state["raised"]:
                state["raised"] = True
                raise exc
            yield article

    monkeypatch.setattr(sources, "iter_articles", fake)
    monkeypatch.setattr(sources.time, "sleep", lambda *_: None)  # no real backoff
    return state


def test_transient_failure_is_retried_and_nothing_is_skipped(monkeypatch):
    _flaky_source(4, _Boom(503), monkeypatch)

    got = [a["id"] for a in sources.iter_articles_resilient("tiny")]

    # Every article exactly once: the replay must not re-deliver the prefix, and
    # the restart must not skip past the point of failure.
    assert got == [str(i) for i in range(10)]


def test_permanent_failure_is_not_retried(monkeypatch):
    # Retrying a 404 six times just hides a clear error behind six backoffs.
    _flaky_source(2, _Boom(404), monkeypatch)

    with pytest.raises(_Boom):
        list(sources.iter_articles_resilient("tiny"))


def test_dropped_read_is_treated_as_transient(monkeypatch):
    dropped = type("ChunkedEncodingError", (Exception,), {})()
    _flaky_source(3, dropped, monkeypatch)

    got = [a["id"] for a in sources.iter_articles_resilient("tiny")]

    assert got == [str(i) for i in range(10)]


def test_restart_budget_is_exhausted_by_consecutive_failures(monkeypatch):
    def always_fails(profile):
        raise _Boom(503)
        yield  # pragma: no cover

    monkeypatch.setattr(sources, "iter_articles", always_fails)
    monkeypatch.setattr(sources.time, "sleep", lambda *_: None)

    with pytest.raises(_Boom):
        list(sources.iter_articles_resilient("tiny", max_restarts=3))


def test_budget_resets_on_progress(monkeypatch):
    # Failures spread across a long run must be survivable; only CONSECUTIVE
    # ones are fatal. Without the reset, a two-hour ingest would die on its
    # seventh unrelated blip.
    fails = {4: False, 7: False}

    def fake(profile):
        for index, article in enumerate(_articles(10)):
            if index in fails and not fails[index]:
                fails[index] = True
                raise _Boom(503)
            yield article

    monkeypatch.setattr(sources, "iter_articles", fake)
    monkeypatch.setattr(sources.time, "sleep", lambda *_: None)

    got = [a["id"] for a in sources.iter_articles_resilient("tiny", max_restarts=1)]

    assert got == [str(i) for i in range(10)]
