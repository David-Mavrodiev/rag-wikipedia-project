# Step 3: a relevance gate, fitted, tested, and costed

The baseline ended with a number and a hypothesis. The number: retrieval accepts
**16 of the 20** held-out unanswerable questions (false_accept 0.800), and every
fabrication in the first end-to-end run came from one of those. The hypothesis,
written down before any code: *"a relevance score that refuses those three
questions before generation removes them."*

This is that change, measured.

## What it is

The IDF coverage gate asks whether a question's rare words appear in the
retrieved text. A neighbouring article supplies them - which is why 16 of 20 get
through. A cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`, 22M
parameters) reads question and passage *together* and scores whether the passage
is about what was asked. Retrieval attaches that score; the gate refuses below a
threshold.

Two decisions worth arguing with:

- **Relevance is checked before the lexical tests and before the
  high-confidence vector escape hatch.** The order is the intervention: these
  false accepts ARE confident vector matches that clear both. A reranker that
  only reordered results would leave every one of them in.
- **Failure is open and loud.** A model that will not load logs an error once
  and retrieval keeps the old gate. This is a quality filter, not an access
  control - `providers/azure_search.py` fails closed because there, passing
  through leaks data.

## Fitted on one suite, tested on another

The threshold was swept on `serving_golden` (90 title-naming questions) and
committed at **0.9** before the gate was run on `serving_detail` (80 title-free
questions). Ten thresholds, whole curve committed:
[`backend/eval/sweep/serving_golden.relevance.json`](../backend/eval/sweep/serving_golden.relevance.json).

| threshold | recall@5 | false_accept | answerable_refusal |
|---|---:|---:|---:|
| 0.0 (off) | 0.933 | 0.500 | 0.033 |
| 0.5 | 0.933 | 0.200 | 0.067 |
| **0.9** | **0.933** | **0.100** | **0.067** |
| 0.95 | 0.933 | 0.100 | 0.067 |
| 0.99 | 0.900 | 0.000 | 0.100 |

0.9 is the last point before recall degrades, and it sits inside the flat
0.9-0.95 plateau rather than on a cliff. 0.99 drives false accepts to zero here
and starts eating recall, which is the trade this project does not make quietly.

**The fit under-predicted the cost, and that is the point of a test suite.** On
`serving_detail`, at retrieval level:

| | gate off | gate 0.9 |
|---|---:|---:|
| false_accept_rate | 0.800 | **0.000** |
| refusal_accuracy | 0.200 | 1.000 |
| answerable_refusal_rate | 0.033 (2/60) | 0.133 (8/60) |
| recall@5 | 0.950 | 0.867 |

Four times the refusal cost the fit suite predicted. A threshold fitted on
questions that name their article is too strict for questions that do not: the
cross-encoder has less to anchor on, scores run lower, and a fixed threshold
refuses more. Note what the recall drop is NOT: `run_eval` scores a refused
answerable case as recall 0.0 by construction, so that 0.083 is the refusals
being counted, not ranking getting worse. Ordering improved - mrr rose from
0.904 to 0.933 on the fit suite from the reranking alone.

## End to end, which is what a user meets

80 cases, llama3.2:3b, Tesla T4, commit `da13cb4`. Reports:
[`backend/eval/e2e/2026-10-16-gate09/`](../backend/eval/e2e/2026-10-16-gate09/).

| | gate off | gate 0.9 |
|---|---:|---:|
| false_accept_rate | 0.150 (3/20) | **0.000 (0/20)** |
| refusal_accuracy | 0.850 | 1.000 |
| answerable_refusal_rate | 0.150 (9/60) | 0.183 (11/60) |
| supported claims, mean per answer (judge) | 0.812 over 48 answers | 0.837 over 43 |
| claims the judge flagged | 9 of 50 | 7 of 43 |
| citation resolves | 1.000 | 1.000 |
| literal `[n]` | 0.000 | 0.000 |
| latency p50 | 1.79 s | 2.42 s |

**The three fabrication sources are gone.** `sd-u-006` (etymology of "oil" for a
question about non-aromatic hydrocarbons), `sd-u-010` (the AIM-54's victories
for a different missile) and `sd-u-016` (a Basque city given Valdivia's rivers)
are each refused before generation. That is the hypothesis, confirmed on the
suite it was written about.

**The cost is three refusals, and all three were unanswerable in practice.**
`sd-a-002`, `sd-a-027` and `sd-a-028` are newly refused, and every one of them
has `context_hit: false` - the article holding the answer was never retrieved,
so the alternative was not a correct answer but an ungrounded one. One case the
baseline refused (`sd-a-039`) is now answered, for a net of two.

**The refusal moved from the model to the gate, which is the part that matters
for an operator.** Baseline: 20 soft refusals (the 3B model declining on its
own) against 6 explicit gate refusals. With the gate: 3 soft, 28 explicit. The
same caution, relocated from a small model's temperament to a threshold that was
fitted, committed, and can be audited, logged and changed.

## What this does not show

- **Twenty unanswerable questions.** 0.000 means "none of 20", not "never". One
  case is five points.
- **The cost does not transfer between question styles** - demonstrated above,
  not speculated about. A deployment whose users ask title-free questions should
  re-fit rather than inherit 0.9.
- **`context_hit_rate` falls 0.950 to 0.867 by construction**: a refused case has
  no context to hit.
- **`supported_sentence_rate` is a mean over answers**, not a ratio of claims:
  each answer contributes its own supported fraction, then those are averaged.
  An earlier version of this table wrote it as "39/48", which reads as 39 of 48
  claims and is wrong - 48 is the number of answers that had judgeable claims.
  The claim counts are in their own row.
- **The judge still grades this**, with the limits measured in
  [judge-accuracy.md](judge-accuracy.md): on blind labels it misses more
  unsupported claims than it invents, so the supported-claim rates in that table
  are not floors.
- **Latency grew 35%** for a cross-encoder pass over 20 candidates on a T4. On a
  CPU-only deployment that is a different number, unmeasured here.

## Reproduce

```bash
SHA=$(git rev-parse HEAD)
modal run infra/modal/demo.py::sweep_relevance --suite serving_golden --thresholds 0.0,0.5,0.9,0.99 --git-sha $SHA
modal run infra/modal/demo.py::sweep_relevance --suite serving_detail --thresholds 0.0,0.9 --git-sha $SHA
modal run infra/modal/demo.py::evaluate --suite serving_detail --rerank 0.9 --out-name e2e-gate09 --git-sha $SHA
```

One at a time: this module defines a web endpoint and concurrent runs fight over
its label. `--out-name` is a name, not a path, because Git Bash rewrites
`/reports/...` into `C:/Program Files/Git/reports/...` and a run that cost real
money wrote its report where no volume was mounted.

## Does the gate earn its place against a bigger model?

The question a client asks first is not "is your gate clever", it is "would you
ship a 3B model at all". So the same suite was run four ways - same corpus, same
retrieval, same judge, same T4 - and the gate has to justify itself against
simply spending more on the model.

| | false_accept | answerable_refusal | supported, mean/answer | flagged claims | answered | p50 |
|---|---:|---:|---:|---:|---:|---:|
| llama3.2:3b | 0.150 | 0.150 | 0.812 | 9 of 50 | 54 | 1.79 s |
| 3b + gate 0.9 | **0.000** | 0.183 | 0.837 | 7 of 43 | 49 | 2.42 s |
| llama3.1:8b | **0.000** | **0.083** | 0.881 | 7 of 71 | 55 | 3.53 s |
| 8b + gate 0.9 | **0.000** | 0.167 | **0.896** | 5 of 62 | 50 | 4.25 s |

**The 8B model reaches zero false accepts on its own, and refuses fewer
answerable questions doing it** - 5 of 60 against the 3B's 9, and against 11
with the gate. It also supports more of its claims over a larger claim base: 71
judged claims to the 3B's 50, because it writes longer answers, and still a
higher supported rate. On quality it beats the gate outright.

**On top of the 8B model the gate is redundant.** False accepts are already
zero, so it buys nothing, and it doubles answerable refusals (0.083 to 0.167)
for another 0.7 s. The one thing it still improves is per-answer groundedness
(0.881 to 0.896, 5 flagged claims of 62 against 7 of 71), which is a trade worth
making only where a wrong answer is expensive and a refusal is cheap.

So the honest recommendation, by deployment rather than by preference:

| situation | choice |
|---|---|
| GPU headroom for an 8B model | **8B, no gate.** Better on every quality axis than 3B+gate, for 1.1 s more. |
| Small GPU, or latency budget under 3 s | **3B + gate.** It buys the 8B's false-accept behaviour at 2.4 s and ~2 GB of VRAM instead of ~5 GB. |
| A wrong answer is catastrophic, a refusal is cheap | **8B + gate**, accepting 1 refused answerable question in 6. |
| Nothing is catastrophic and speed matters | 3B alone, knowing 3 of 20 unanswerable questions get answered. |

That is what the gate is for: making a small model behave like a larger one on
the axis that produces fabrications, cheaply. It is not a substitute for a
better model, and this table is the reason it ships off by default.

**Caveat, same as everywhere here.** Twenty unanswerable questions and sixty
answerable ones. The 8B's 0.000 is "none of 20", and the gap between 0.083 and
0.150 answerable refusal is four cases. Both models were run once; the earlier
pair of identical T4 runs says generation is reproducible at this temperature,
but these are single runs per configuration.
