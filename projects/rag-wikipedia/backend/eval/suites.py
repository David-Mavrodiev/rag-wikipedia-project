"""Which corpus each evaluation suite describes.

A suite's expectations are facts about ONE corpus. The fixture suites name
articles in the committed 150-article fixture; the serving suites were drawn
from the 24,694-article `real` ingest and its held-out slice. Scored against
the wrong corpus a suite still produces numbers - confident, well-formatted and
meaningless: the fixture's out-of-corpus questions are answerable from 25k
articles, and the serving suite's expected titles mostly do not exist in 150.

The Makefile pins each suite to its collection. This table is the same fact in
code, so a runner that is expensive to get wrong (an end-to-end run is an hour
of generation) can refuse the mismatch before spending it.

  golden, holdout, adversarial  the original fixture suites; every answerable
                                question names its article ("What is X?")
  detail                        fixture; questions answered by one passage
                                that do NOT name the article
  serving_golden                real; title questions + held-out refusals
  serving_detail                real; title-free passage questions + held-out
                                passages whose answer occurs nowhere indexed
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EVAL_DIR = Path(__file__).parent

# Unicode letters and digits: "[a-z0-9]+" split "César Ramírez" into "c", "sar",
# "ram", "rez" - and then "K. C. Jones" read as naming that article.
_WORD = re.compile(r"[^\W_]+")
_QUALIFIER = re.compile(r"\s*\([^()]*\)\s*$")
# Words too common to count as naming an article. Deliberately short: a longer
# list would let a question that names its article slip through.
_TITLE_STOPWORDS = frozenset({"a", "an", "the", "of", "and", "in", "on", "for", "to", "at", "by"})

SUITE_PROFILES = {
    "golden": "fixture",
    "holdout": "fixture",
    "adversarial": "fixture",
    "detail": "fixture",
    "serving_golden": "real",
    "serving_detail": "real",
}


class SuiteProfileMismatch(ValueError):
    """A suite was about to be scored against a corpus it does not describe."""


def suite_path(name: str) -> Path:
    if name not in SUITE_PROFILES:
        raise ValueError(f"unknown suite {name!r}; known: {', '.join(SUITE_PROFILES)}")
    return EVAL_DIR / f"{name}.jsonl"


def load_suite(name: str) -> list[dict]:
    return [
        json.loads(stripped)
        for line in suite_path(name).read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip())
    ]


def title_words(title: str) -> frozenset[str]:
    """The words that NAME an article: its title minus a trailing disambiguator.

    "Mercury (planet)" is named by "mercury"; "planet" only says which Mercury,
    so a question may use it.
    """
    name = _QUALIFIER.sub("", title).lower()
    return frozenset(word for word in _WORD.findall(name) if word not in _TITLE_STOPWORDS)


def title_acronym(title: str) -> str | None:
    """"American Football Conference" -> "afc". None under three letters: a
    two-letter acronym ("Aegean Sea" -> "as") collides with ordinary words."""
    name = _QUALIFIER.sub("", title)
    initials = [w[0] for w in re.findall(r"[^\W\d_]+", name) if w.lower() not in _TITLE_STOPWORDS]
    return "".join(initials).lower() if len(initials) >= 3 else None


def names_its_title(question: str, title: str) -> set[str]:
    """Title words - or the title's acronym - that appear in the question.
    Empty = title-free.

    A detail question must not name its article: "What is Biotite?" is answered
    by any retriever that matches titles, which is all the title suites measure.
    An acronym names it just as well - review found "AFC" standing in for
    "American Football Conference". Derived words ("astrologer" for Astrology)
    are NOT caught here: a stem rule flagged too much ordinary English to run
    unattended, so they are a review item instead.
    """
    words = set(_WORD.findall(question.lower()))
    named = set(title_words(title) & words)
    acronym = title_acronym(title)
    if acronym and acronym in words:
        named.add(acronym)
    return named


def normalize(text: str) -> str:
    """Case- and whitespace-insensitive form for answer containment checks."""
    return " ".join(text.casefold().split())


def check_profile(name: str, profile: str) -> None:
    expected = SUITE_PROFILES.get(name)
    if expected is None:
        raise ValueError(f"unknown suite {name!r}")
    if expected != profile:
        raise SuiteProfileMismatch(
            f"suite {name!r} describes the {expected!r} corpus, but PROFILE={profile!r}. "
            f"Scoring it here would measure nothing - use the paired make target."
        )
