"""The held-out slice must be stable, unbiased, and actually enforced.

Out-of-corpus eval cases are only valid if the articles they are built from are
genuinely absent. That guarantee rests entirely on this rule, so it is tested
rather than assumed.
"""

import hashlib

import pytest
from app.core.holdout import HOLDOUT_MODULUS, held_out_fraction, is_held_out


def test_decision_is_deterministic():
    # The whole design depends on this: a resumed or repeated ingest must hold
    # out exactly the same articles, or one could slip into the corpus and
    # silently invalidate every question built from it.
    ids = [str(i) for i in range(500)]
    first = [is_held_out(i) for i in ids]
    second = [is_held_out(i) for i in ids]
    assert first == second


def test_not_python_hash_which_is_salted_per_process():
    # PYTHONHASHSEED randomises hash() per process, which would select a
    # different 1% on every run. Pin the derivation to SHA-256 explicitly.
    article_id = "12345"
    digest = hashlib.sha256(article_id.encode("utf-8")).digest()
    expected = int.from_bytes(digest[:8], "big") % HOLDOUT_MODULUS == 0
    assert is_held_out(article_id) is expected


def test_int_and_str_ids_agree():
    # HF gives string ids, but callers should not have to care.
    assert is_held_out(12345) == is_held_out("12345")


def test_roughly_the_intended_fraction():
    n = 20_000
    held = sum(1 for i in range(n) if is_held_out(i))
    expected = n * held_out_fraction()
    # Generous bounds: this asserts the hash distributes, not that it is perfect.
    assert 0.5 * expected < held < 1.6 * expected, f"{held} of {n}"


def test_selection_is_independent_of_stream_order():
    # Position in the stream must not influence the decision, or a differently
    # ordered dump would hold out a different set.
    ids = [f"article-{i}" for i in range(1000)]
    held = {i for i in ids if is_held_out(i)}
    assert {i for i in reversed(ids) if is_held_out(i)} == held


@pytest.mark.parametrize("article_id", ["", "0", "999999999", "a-very-long-" * 20])
def test_never_raises_on_odd_ids(article_id):
    assert isinstance(is_held_out(article_id), bool)
