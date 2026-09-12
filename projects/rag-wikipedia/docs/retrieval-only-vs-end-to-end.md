# Retrieval-only vs end-to-end: what `false_accept_rate = 0.600` actually measures

> **UPDATE 2026-09-12 — the gate now fires.** The measurement below stands as
> taken, and its first recommendation has since been carried out.
> `refusal_min_evidence_coverage` was measured on the serving corpus with
> `scripts/sweep_coverage.py` and enabled at **0.45**: `false_accept_rate`
> 0.600 → 0.500 there, and on the fixture suites golden and adversarial
> 0.500 → 0.400 at no cost in false refusals (holdout is unchanged at 0.600).
> So the central finding below — that the evidence gate refuses nothing and the
> model does all the out-of-corpus work — describes the system *before* that
> change. What has **not** been re-measured is the end-to-end split, and whether
> the LangGraph rewrite branch is now reachable. Both need a re-run.

The published `false_accept_rate` is a **retrieval-only** number. It scores the
decision `retrieve()` makes, with no model involved. Measured end-to-end — the
whole engine, model included — the same corpus and the same suites give 0.000 and
0.100 instead of 0.600 and 0.500.

That gap is not good news. Reconstructed case by case, it says something sharper:

> On these suites the **evidence gate refused nothing at all**. Every
> retrieval-level refusal came from the regex intent filter, and every genuine
> out-of-corpus judgement was made by the language model.

The system's ability to decline a question it cannot support currently rests on
`llama3.2:3b` being willing to say "I don't know" — a property that is now one
environment variable away from being swapped out.

## The two measurements

| | what decides `refused` | where it comes from |
|---|---|---|
| **Retrieval-only** | `retrieve()`: intent filter, score floor, evidence gate | `make audit-baseline` → `audit_report.json`, and the delta CI posts on a PR |
| **End-to-end** | the whole engine: retrieval **and** the model's own refusal | `make compare-engines` → `engine_comparison.json` |

Both are legitimate. They answer different questions, and quoting one as if it
were the other is the mistake this note exists to prevent.

## The reconstruction

`engine_comparison.json` records `llm_calls` per case, which classifies every
refusal without ambiguity: a refusal costing **zero** model calls was made by
retrieval; a refusal costing one was made by the model after retrieval accepted
the question.

Before trusting that split, it has to reproduce the published number. It does,
exactly, on both suites:

| suite | unanswerable | refused by retrieval | reached the model | model refused | answered anyway |
|---|---:|---:|---:|---:|---:|
| `holdout` | 5 | 2 | 3 | 3 | **0** |
| `adversarial` | 10 | 5 | 5 | 4 | **1** |

"Reached the model" is what retrieval accepted, so it *is* the retrieval-only
false-accept count: 3 of 5 is 0.600 on holdout, 5 of 10 is 0.500 on adversarial —
the figures in `audit_report.json`, arrived at from the other direction.

End-to-end, of the 8 unanswerable questions retrieval let through, **the model
refused 7**.

## The finding: the evidence gate never fired

`DirectEngine` labels every retrieval-level refusal coarsely as `no_evidence`, so
the reason alone cannot say *which* guard stopped a question. The LangGraph engine
labels them precisely, because its triage node runs the intent filter as its own
step. Cross-referencing the two engines case by case over the same 40 questions:

- retrieval-level refusals: **7**
- of those, attributed to the intent filter (`is_private_or_time_dependent`): **7**
- attributed to the evidence gate — score floor, term overlap, coverage: **0**

The two sets are identical. The evidence gate did not refuse a single case.

That is consistent with what the README already says about the gate weakening as
the corpus grows: `refusal_min_overlap_terms = 1` accepts on one shared token, so
in practice it accepts everything. This measurement puts a count on it rather than
an explanation.

**What retrieval caught** — all private or time-bound, all matched by regex before
any search runs:

> *What is my employee identification number?* · *What meetings do I have
> tomorrow?* · *What is my Apollo account password?* · *Where did I save my
> Apollo 11 notes yesterday?* · *What is the current albedo outside my window?* ·
> *Will my Alaska trip be delayed tomorrow?* · *What is my private Aristotle essay
> grade?*

**What reached the model** — ordinary questions whose answers a pretrained model
knows, but which this corpus does not support:

> *What is the capital of Canada?* · *What is the boiling point of water?* · *Who
> composed the Fifth Symphony?* · *What is the Python programming language?* ·
> *Who was Apollo Creed?* · *What is alkaline battery chemistry?* · *Who directed
> the film Alien?* — each refused by the model.

The one that got through: *How much did the Alaska Purchase cost in modern
dollars?* — the corpus has the article, but not the figure. A retrieved chunk
looked close enough to both guards.

## Why this also explains the LangGraph result

The graph engine's rewrite branch executed zero times across the same 40 cases.
It is reached only when `decide_evidence` refuses — and `decide_evidence` never
refuses. The retry loop was not unlucky; it was unreachable by construction. Any
future verdict on that pattern has to wait until the gate it depends on can fire.

## What this changes, and what it does not

**Changes.** The headline number should be read as "the evidence gate is not
working", not "the system answers 60% of questions it should decline". End-to-end
it declines nearly all of them — for now.

**Does not change.** No gate is relaxed and no threshold moves. If anything the
case for fixing the evidence gate is stronger: the current safety behaviour is a
property of one model. Swap the model — which the `LLM` contract now makes a
one-value change, and which the planned Vertex adapter will actually do — and a
more compliant model could answer all eight. Nothing in the test suite would fail,
because no test asserts that the *model* refuses.

**Therefore:** this measurement must be re-run per provider, not once.

## Limits

- One corpus (the committed 150-article fixture, `wikipedia_eval`, 2,422 vectors),
  two suites, **40 cases**, of which only 15 are unanswerable. Denominators of 5
  and 10 are small; a single case moves `false_accept_rate` by 0.100 or 0.200.
- One model, `llama3.2:3b`, at temperature 0 with a pinned seed.
- The `golden` suite was **not** run end-to-end; it is retrieval-only here.
- Nothing here measures the serving corpus (87,173 vectors). The README's
  0.600-at-24,694-articles figure is a different suite on a different corpus, and
  this note does not qualify it.
- The model's caution has a price that shows in the same run: one answerable
  holdout question was refused by the model despite good retrieval.

## Next

1. Measure a coverage threshold with `scripts/sweep_coverage.py` and enable
   `refusal_min_evidence_coverage`, so the evidence gate can refuse at all.
2. Re-run this measurement; the gate's contribution should stop being zero.
3. Only then re-run the engine head-to-head: the rewrite branch becomes reachable,
   and the LangGraph engine gets a fair test.
4. Re-run it again against each new provider — starting with Vertex AI.

## Provenance

Retrieval-only figures: `backend/eval/audit_report.json`, generated at commit
`0301d3a`, profile `fixture`, collection `wikipedia_eval`, 2,422 vectors,
`BAAI/bge-small-en-v1.5`, k=5. End-to-end figures:
`backend/eval/engine_comparison.json`, 2026-09-10, same corpus and embedder,
`llama3.2:3b`, temperature 0, seed 0, zero errored cases. Written 2026-09-12.
