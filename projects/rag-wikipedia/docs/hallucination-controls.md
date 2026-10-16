# Hallucination controls - what to take from TypeSafe's jev, and what to measure

> **Status: proposal, 1 Oct 2026.** Nothing here is implemented. It is written
> against the measured baseline ([measurement-baseline.md](measurement-baseline.md))
> and the judge's own accuracy ([`backend/eval/judge/LABELS.md`](../backend/eval/judge/LABELS.md)),
> and it says for each idea what number would have to move.

## What jev is

[TypeSafe AI](https://typesafe.ai/)'s jev is a hosted model, not an evaluation
tool: a "System One model" that answers **narrow typed questions** instead of
generating text. Three question types ([docs](https://docs.typesafe.ai/primitives.md)):
`Choice` (pick one option), `Score` (rate against ordered levels), `Noul`
(probability that a yes/no statement is true). Every answer carries a
probability distribution and a confidence, and their guidance is to branch on
it: act when confidence is high, review in the middle, escalate when it is low
([confidence](https://docs.typesafe.ai/confidence.md)). Schema validity is
guaranteed by construction - they put it as "schema matching is guaranteed, thus
we can confidently add 0% into the plots" - which is a claim about types, not
about truth.

Their own cookbooks apply this to exactly our problem:
[classifying RAG passages](https://docs.typesafe.ai/cookbooks/classifying_rag_passages.md)
asks four yes/no questions per retrieved passage (is it relevant, does it
contain answer evidence, does it contradict the question's premise, does it try
to instruct the system) and routes on thresholds **before** the answering model
runs; [citation check](https://docs.typesafe.ai/cookbooks/citation_check.md)
asks one three-way question per claim - `supports` / `contradicts` /
`says_nothing` - and auto-accepts only above 0.8 confidence.

What does not carry over: it is a paid API, and this project is deliberately
local and framework-free. Their numbers are also their own - their workflow
evals are built by their own team, and the "0%" is a type guarantee. So what
follows takes the *design*, implemented with local models, and measures it here.

## What is worth taking

The baseline says where the hallucinations actually come from: of 11 flagged
claims, 2 were fabrications and 1 was a grounded answer to the wrong question -
and **all three were answers to questions the system should have refused**. The
gate, not the generator, is the leak.

### 1. Typed output instead of a parsed one (highest value, lowest risk)

`[n]` reached users in 8 of 55 answers because the prompt asked for citation
markers in prose and the model copied the placeholder. Ollama supports
schema-constrained decoding (`format` with a JSON schema), so the answer can
come back as `{"sentences": [{"text": ..., "cites": [1, 2]}], "answerable":
bool}` with citations as integers validated against the context length. That
removes the whole failure class rather than correcting the wording of a prompt -
the one jev lesson that is a guarantee and not a probability.

*Moves:* `placeholder_citation_rate` 0.145 -> 0 by construction, and makes
per-sentence citation checking possible (below). *Risk:* a 3B model under a
grammar constraint may answer worse; measured by re-running the end-to-end
suite, not by inspection.

### 2. A per-passage evidence question before generating

Today the gate is a cosine floor plus IDF coverage, and on title-free questions
it wrongly accepts **16 of 20** held-out questions: the question's rare words
are present, in neighbouring articles that do not answer it. jev's passage
questions are the shape of the fix, and the local equivalents already exist:
a cross-encoder relevance score (`cross-encoder/ms-marco-MiniLM-L-6-v2`) and a
"does this passage contain the answer" score (a QNLI cross-encoder). Refuse when
no passage clears the evidence threshold; that is step 3 of the plan, stated as
a probability per passage rather than a re-sort.

*Moves:* retrieval `false_accept` 0.800 on `serving_detail`, and with it the
end-to-end 0.150 - the run's only fabrications sit behind it. *Watch:*
`answerable_refusal_rate`, which a stricter gate raises; it is already 0.133
end to end because the model refuses answers retrieval accepted.

### 3. A calibrated confidence, and a review band in the UI

The API answers or refuses, binary. A calibrated score (the passage evidence
probability, and after generation the support score) supports three outcomes:
answer, answer **marked low-confidence**, refuse. The `cited` flag added in
step 2 is the primitive version of this; the honest version reports calibration,
which `eval/judge_accuracy.py` already computes (expected calibration error and
a score-versus-reality table) for the judge and would compute the same way for
the gate.

*Moves:* nothing by itself - it is how a user sees uncertainty instead of a
confident wrong answer. *Requires:* thresholds fitted on a calibration split,
never on the suites (see below).

### 4. Per-sentence citation verification at serving time

jev's citation check maps onto what the harness already does offline: for each
answer sentence, ask whether the **cited** chunk supports it, three ways
(`supports` / `contradicts` / `says_nothing`). Unsupported sentences can be
dropped, or the answer marked. The measured cost decides the design: the
offline judge takes ~16 s per answer on this CPU, so serving needs a small model
(`nli-deberta-v3-xsmall` or a MiniLM cross-encoder) over the cited chunk only,
with a measured latency budget against the 7 s generation.

*Moves:* the share of answers reaching a user with an unsupported sentence.
*Hard constraint:* see the next section - the serving guard must not be the
model that grades us.

### 5. Atomic questions over one composite judgement

jev's composite-scoring pattern is to break a judgement into atomic scores and
combine them in code with weights you control. Our judge is sentence-level and
its errors are exactly composition errors: 6 of 8 false alarms were claims
joining facts from separate sentences. `sentence_support(assemble=k)` is the
first move in that direction, and whether it helps is a measurement, not an
argument.

## Keeping the harness honest while the RAG gets better

The request this proposal has to satisfy is "the RAG avoids hallucinations while
the harness stays accurate and does not overfit the test questions". Four rules,
and they are the point of the whole document:

1. **The serving guard and the evaluation judge are never the same model.** If
   the RAG filters its answers with the model that later scores them, the
   groundedness number measures the filter, not the answers: everything the
   judge would flag has already been removed. Serving gets the small NLI model;
   evaluation keeps `nli-deberta-v3-base`, plus the hand labels. Any overlap
   gets stated in the report.
2. **Thresholds are fitted on data no suite contains.** `scripts/build_detail_suite.py`
   can draw a `calibration` suite with a different seed, and
   `scripts/build_judge_set.py` already builds claims from passages no suite
   uses, split dev/test by passage. Thresholds move on dev; the suites are
   touched once, afterwards.
3. **Thresholds are committed before the test run, not after it.** A threshold
   chosen by looking at the test result is a fitted parameter wearing the
   costume of a measurement. The commit order is the evidence.
4. **A red gate is never relaxed to pass.** The gates are red on purpose
   (`false_accept_rate_max` 0.1 against 0.150 measured). If a change cannot move
   the number, the number stays red and the change does not ship as an
   improvement.

And one limit to keep stating: groundedness is not correctness. `sd-u-010`
quotes its context faithfully about the wrong missile - supported, and wrong.
No entailment judge catches that, which is why `answers_question` is a separate
field in the labels and why refusal accuracy, not groundedness, is the gate.

## Order, if this is taken up

1. Typed output (§1) - removes a class, needs no new model.
2. Evidence question per passage (§2) - the step-3 cross-encoder, with refusal
   decided by a probability. Judge-accuracy work (`assemble`) lands first, so the
   groundedness number used to compare is itself measured.
3. Calibration + review band (§3), then serving-time verification (§4).

Each step: build the calibration split, fit, commit, then one run on the suites.
