"""Is a generated answer supported by what the model was given?

The number this replaces blended three different failures into one average.
`eval.metrics.groundedness` is a lexical overlap - the share of an answer's
words that also occur in its context - and run_eval.py averaged it over EVERY
answerable case, refusals included. The refusal sentence "I don't know based on
the provided context." is eight words; scored as if it were an answer it lands
on 1/8, 2/8 or 3/8. A partial serving run (2026-09-21, 33 cases) averaged 0.700
against a median of 0.850, and all eight scores under 0.5 were exact multiples
of 1/8: refusals, reported as poorly grounded answers.

So an answer is judged here only if it IS an answer, and three separate
questions are asked of it:

1. CITATIONS. Does it cite a chunk the model was actually given? Checked with
   the application's own `extract_citation_indices`, so "valid" means the UI
   would render a source - the thing a user sees. A literal "[n]" is recorded
   separately: the prompt says "Cite inline with [n] markers" and llama3.2:3b
   sometimes copies the placeholder instead of a number.
2. LEXICAL OVERLAP. The old metric, kept under an honest name and computed over
   answers only, so earlier readings remain explicable.
3. SUPPORT. Is each claim-sentence ENTAILED by the context? A natural-language
   inference cross-encoder scores (premise, hypothesis=sentence) pairs; a
   sentence counts as supported when its best premise reaches
   SUPPORT_THRESHOLD. This is the groundedness number.

WHY "BEST PREMISE" AND NOT ONE PREMISE. The judge is brittle to what surrounds
the evidence, and no single premise size works. Measured on a four-sentence
Lincoln passage with nli-deberta-v3-base (2026-09-21):

    claim                                       window  sentence  adjacent pair
    "...was the 16th president of the US."       0.950     0.997          0.996
    "...led the country through the Civil War."  0.131     0.996          0.996
    "The 16th US president issued the E.P."      0.996     0.000          0.971
    "Lincoln was a lawyer in Illinois."          0.996     0.002          0.028
    "Lincoln was born in 1920." (false)          0.000     0.000          0.000

A whole window missed a claim one sentence states outright; single sentences
missed claims that need an earlier sentence to resolve "he". Earlier, the same
true claim scored 0.007 against a three-sentence window because of a
parenthetical date range. So each claim is judged against every window, every
single sentence and every adjacent pair, each also with parentheticals
stripped, and the best score counts - the SummaC-style reading. The false claim
stayed at 0.000 under every variant: taking the maximum did not manufacture
support. Sentences and pairs are only judged when they share a content word
with the claim, which bounds the cost; whole windows are always judged.

The judge runs on CPU by default: on the reference laptop the GPU is the thermal
bottleneck, and scoring must not heat the card the next generation runs on.

Limits, stated rather than hidden: sentence splitting is a regex and can break
on abbreviations ("St. Louis"); an NLI model trained on short premises is
weaker on long ones, which is why context is read in overlapping windows; and a
sentence with no entailing window is "unsupported by the context", which is not
the same as false.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from app.core.citations import extract_citation_indices

DEFAULT_NLI_MODEL = "cross-encoder/nli-deberta-v3-base"
SUPPORT_THRESHOLD = 0.5
WINDOW_WORDS = 180
MIN_CLAIM_WORDS = 3

PLACEHOLDER = re.compile(r"\[\s*n\s*\]", re.IGNORECASE)
# Citation-like markers are stripped before a sentence is judged: "[1]" is not
# part of the claim, and an NLI model reading it is reading noise.
_MARKERS = re.compile(r"\s*\[(?:\s*\d+\s*(?:,\s*\d+\s*)*|\s*n\s*)\]", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])|\n+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_PARENTHETICAL = re.compile(r"\s*\([^()]*\)")
# Unicode letters and digits: "[a-z0-9]+" split "César Ramírez" into "c", "sar",
# "ram", "rez" - and then "K. C. Jones" read as naming that article.
_WORD = re.compile(r"[^\W_]+")
_STOPWORDS = frozenset(
    "the and for was were are is its his her their with from that this which who whom "
    "has have had not but also into than then there these those they them been being "
    "one two about after before over under between during while where when what how".split()
)


@dataclass(frozen=True)
class CitationCheck:
    cited: tuple[int, ...]
    resolvable: tuple[int, ...]
    placeholder: bool

    @property
    def valid(self) -> bool:
        """At least one citation points at a chunk the model was given."""
        return bool(self.resolvable)


def check_citations(answer: str, n_context: int) -> CitationCheck:
    cited = tuple(extract_citation_indices(answer))
    resolvable = tuple(index for index in cited if 1 <= index <= n_context)
    return CitationCheck(cited, resolvable, bool(PLACEHOLDER.search(answer)))


def _sentences(text: str, min_words: int) -> list[str]:
    parts = (_BULLET.sub("", part).strip() for part in _SENTENCE_BREAK.split(text))
    return [part for part in parts if len(part.split()) >= min_words]


def claim_sentences(answer: str, *, min_words: int = MIN_CLAIM_WORDS) -> list[str]:
    """The sentences an answer asserts, with citation markers removed.

    Fragments under `min_words` are dropped: a stray "Yes." or a heading has no
    claim to check, and scoring it would move the rate without meaning anything.
    """
    return _sentences(_MARKERS.sub("", answer), min_words)


def premise_windows(text: str, *, max_words: int = WINDOW_WORDS) -> list[str]:
    """Overlapping sentence windows of a context chunk.

    Chunks are 512 cl100k tokens; with a hypothesis added that overflows the
    judge's 512-token input and the tail would be truncated away unseen. Windows
    advance by half their length so a claim straddling a boundary is still read
    whole by one of them.
    """
    sentences = _sentences(text, 1)
    lengths = [len(sentence.split()) for sentence in sentences]
    windows: list[str] = []
    start = 0
    while start < len(sentences):
        end, words = start, 0
        # Always take at least one sentence, so an over-long one is still read.
        while end < len(sentences) and (end == start or words + lengths[end] <= max_words):
            words += lengths[end]
            end += 1
        windows.append(" ".join(sentences[start:end]))
        if end >= len(sentences):
            break
        start = max(start + 1, (start + end) // 2)
    return windows


class NLIJudge:
    """Probability that a premise entails a hypothesis."""

    def __init__(
        self,
        model_name: str = DEFAULT_NLI_MODEL,
        *,
        device: str = "cpu",
        batch_size: int = 16,
        model=None,
    ) -> None:
        if model is None:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(model_name, device=device)
        self.model_name = model_name
        self._model = model
        self._batch_size = batch_size
        config = getattr(model, "config", None) or model.model.config
        labels = {int(index): str(name).lower() for index, name in config.id2label.items()}
        entail = [index for index, name in labels.items() if name.startswith("entail")]
        if len(entail) != 1:
            # Read from the model, never assumed: label order differs between
            # NLI checkpoints, and a wrong index silently scores contradiction.
            raise ValueError(f"{model_name} has no single entailment label: {labels}")
        self._entail = entail[0]

    def entailment(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        scores = self._model.predict(
            pairs, batch_size=self._batch_size, apply_softmax=True, show_progress_bar=False
        )
        return [float(row[self._entail]) for row in scores]


def content_terms(text: str) -> frozenset[str]:
    return frozenset(
        word for word in _WORD.findall(text.lower()) if len(word) > 2 and word not in _STOPWORDS
    )


def premise_spans(text: str) -> list[str]:
    """Every single sentence of a chunk, and every adjacent pair."""
    sentences = _sentences(text, 1)
    return sentences + [f"{first} {second}" for first, second in zip(sentences, sentences[1:])]


def _variants(premise: str) -> list[str]:
    stripped = _PARENTHETICAL.sub("", premise)
    return [premise] if stripped == premise else [premise, stripped]


def sentence_support(sentences: list[str], contexts: list[str], judge: NLIJudge) -> list[float]:
    """Best entailment probability of each sentence over every premise it is shown.

    Premises: all windows of all chunks, plus the single sentences and adjacent
    pairs that share a content word with the claim - each as written and with
    parentheticals stripped. See the module docstring for why.
    """
    if not sentences:
        return []
    windows = [window for text in contexts for window in premise_windows(text)]
    if not windows:
        return [0.0] * len(sentences)
    spans = [(span, content_terms(span)) for text in contexts for span in premise_spans(text)]

    pairs: list[tuple[str, str]] = []
    owners: list[int] = []
    for index, sentence in enumerate(sentences):
        terms = content_terms(sentence)
        premises = windows + [span for span, span_terms in spans if terms & span_terms]
        seen: set[str] = set()
        for premise in premises:
            for variant in _variants(premise):
                if variant not in seen:
                    seen.add(variant)
                    pairs.append((variant, sentence))
                    owners.append(index)

    best = [0.0] * len(sentences)
    for owner, probability in zip(owners, judge.entailment(pairs)):
        best[owner] = max(best[owner], probability)
    return best


def lexical_overlap(answer: str, contexts: list[str]) -> float:
    """The pre-2026-09-21 `groundedness`, unchanged, under an accurate name."""
    from eval.metrics import groundedness

    return groundedness(answer, contexts)


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def summarize_answers(records: list[dict], *, threshold: float = SUPPORT_THRESHOLD) -> dict:
    """Answer-quality metrics over the cases the system actually ANSWERED.

    Each record needs `answered` (bool), `answer`, `context` (list of texts), and
    optionally `support` (per-sentence entailment probabilities, None when the
    judge did not run). Refusals are counted, never scored.

    Every answered case counts, including an unanswerable question the system
    wrongly answered: that answer reached a user, and whether it was grounded is
    exactly what matters about it.
    """
    answered = [record for record in records if record["answered"]]
    checks = [check_citations(record["answer"], len(record["context"])) for record in answered]
    summary: dict = {
        "n_answered": len(answered),
        "n_refused": len(records) - len(answered),
        "citation_valid_rate": _mean([float(check.valid) for check in checks]),
        "placeholder_citation_rate": _mean([float(check.placeholder) for check in checks]),
        "lexical_overlap": _mean(
            [lexical_overlap(record["answer"], record["context"]) for record in answered]
        ),
    }

    judged = [record for record in answered if record.get("support") is not None]
    if judged:
        with_claims = [record for record in judged if record["support"]]
        summary.update(
            {
                "support_threshold": threshold,
                "n_judged": len(judged),
                "n_without_claims": len(judged) - len(with_claims),
                "supported_sentence_rate": _mean(
                    [
                        sum(score >= threshold for score in record["support"])
                        / len(record["support"])
                        for record in with_claims
                    ]
                ),
                "fully_supported_answer_rate": _mean(
                    [
                        float(all(score >= threshold for score in record["support"]))
                        for record in with_claims
                    ]
                ),
            }
        )
    return summary
