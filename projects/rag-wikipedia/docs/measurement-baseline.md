# Measurement baseline - before hybrid search and reranking

> **Status: point-in-time record, 21-22 Sep 2026.** The numbers every later change in
> retrieval is judged against. Measured on branch `feat/measurement-v2`, whose
> serving path is `main` at `b395c45` plus two changes that do not alter an answer
> (a `Server-Timing` header on `/query`, and engines reporting the context they
> generated from). Where this disagrees with the repository, the repository is right.

## Why a new baseline was needed

Three things the repository published could not carry the next step:

1. **The suites measured title lookup.** Every answerable question in `golden`
   (40/40) and `serving_golden` (60/60) names its own article - "What is Biotite?".
   A keyword retriever scores perfectly on that by construction, and a reranker has
   nothing left to improve, so neither hybrid search nor reranking could be judged.
2. **Groundedness counted refusals as bad answers.** The lexical score was averaged
   over every answerable case. The refusal sentence is eight words and scored 1/8
   to 3/8; a partial run averaged 0.700 against a median of 0.850, and every score
   under 0.5 was a refusal. No groundedness number had been published, and that one
   should not have been.
3. **Latency had two unlabelled stories.** The demo showed ~3 s; `engine_comparison.md`
   published a 48 s mean. Both were true - on a cool GPU and on a thermally clamped one.

## What changed in the measuring

| | where |
|---|---|
| Two **title-free suites**: a question about a fact in one passage that never names the article. Built by a script that checks corpus properties only - no title word in the question, the answer verbatim in the passage, every unanswerable answer absent from all indexed chunks | `scripts/build_detail_suite.py`, `eval/detail.jsonl` (fixture), `eval/serving_detail.jsonl` (served) |
| **Answers scored only when they are answers**; citations checked with the app's own parser; each claim judged by an entailment model against the context it came from | `eval/grounding.py` |
| **End-to-end runner**: paced by GPU temperature, checkpointed per case, resumable, re-scorable without regenerating | `eval/run_e2e.py`, `make e2e-serving` |
| **GPU conditions** on every timed sample; pacing that waits for the card to cool | `eval/thermal.py`, `compare_engines.py` |
| **Per-request stage timings** (`Server-Timing`) and a latency bench that groups by cold/warm, generation and GPU regime | `app/api/query.py`, `scripts/bench_latency.py` |

The detail questions were drafted from seed-sampled passages (seed 20260921) by an
AI assistant, not by the model under test, and every case carries the passage
sentence holding its answer so it can be reviewed at a glance.

**Review before use.** All 140 cases went through an automated pass - question
words the source article does not contain, words sharing a stem or an acronym with
the title, other articles containing the same answer (ranked by how many question
words they share), and for unanswerable cases any chunk holding all of the
answer's words in any order - and each flag was then read against the rival
article's actual text. Fourteen cases changed:

| problem | cases | example |
|---|---:|---|
| the question named the article by acronym or nickname | 3 | "AFC", "HAIPE", "Oscars" |
| a word shared the title's stem | 3 | "astrologer" for *Astrology* |
| another article answers the same question | 6 | *Black* also says Churchill called his depression "my black dog" |
| the question did not identify one entity | 1 | "the Russian reformer" |
| another article overlapped the fact | 1 | *Angola* also reports the 2022 election |

Each was re-drafted on a fact only its passage states (one was replaced by the next
sampled passage), and the builder re-verified all of them. The review also changed
the tooling: a title acronym now fails the title-free check automatically, case ids
come from the passage rather than a running number, and the end-to-end runner
matches saved answers by question text - keyed by id, an edited question would have
kept the answer to its old wording. What review cannot rule out: an unanswerable
fact present in the corpus in different words, and rivals the answer-string scan
does not see because they phrase the answer differently.

## Retrieval

Retrieval-only: the decision `retrieve()` makes, before any model runs. k = 5, the
IDF coverage gate at 0.45.

<!-- generated from the report files by the baseline script; do not hand-edit -->
| suite | corpus | recall@5 | MRR | precision@5 | refusal_accuracy | false_accept | answerable_refusal |
|---|---|---:|---:|---:|---:|---:|---:|
| `golden` | fixture | 1.000 | 1.000 | 0.930 | 0.600 | 0.400 | 0.000 |
| `holdout` | fixture | 1.000 | 0.967 | 0.573 | 0.400 | 0.600 | 0.000 |
| `adversarial` | fixture | 1.000 | 0.950 | 0.660 | 0.600 | 0.400 | 0.000 |
| `detail` | fixture | 0.950 | 0.950 | 0.655 | 0.800 | 0.200 | 0.025 |
| `serving_golden` | served | 0.933 | 0.904 | 0.383 | 0.500 | 0.500 | 0.033 |
| `serving_detail` | served | 0.950 | 0.950 | 0.297 | 0.200 | 0.800 | 0.033 |

**Dense retrieval does not depend on the title.** Title-free questions over all
24,694 served articles reach recall@5 0.950 - level with the title suite. Hybrid
search has to earn its place against that, not against a weak baseline.

**The refusal gate is much worse on specific questions than on title questions.**
On the served corpus it wrongly accepts 16 of 20 held-out detail questions (0.800,
against 0.500 on `serving_golden`). All 16 pass as `sufficient_evidence_coverage`:
the question's rare words - "Sicilian", "Augustine", "Cornish" - really are in the
top chunks, but of neighbouring articles (*Calavera*, *Augustine of Hippo*,
*Cornwall*) that do not answer it. IDF coverage measures whether a question's
vocabulary is present, not whether a passage answers it. That is the gap a
cross-encoder relevance score is built for, and the hypothesis step 3 tests.

**One article can crowd out the answer.** On `detail`, "In the Iliad, which prophet
identifies the cause of the plague Apollo sends?" retrieves five chunks of *Apollo*
and none of *Achilles*. A reranker that scores relevance rather than shared words,
and a limit on chunks per article, both target this.

## End to end (answers)

`serving_detail` on the served corpus: all 80 cases answered by llama3.2:3b
through the direct engine, and every answer's claims judged against the context
it was generated from. Answers were generated at `2bcc858` on 21-22 Sep, paced at
90/87 C (61 pauses, 94 min of cooling); they were scored at `9d14a3b`, after the
claim splitter stopped cutting sentences at initials such as "Donald J." (that
fix alone moved the supported-claim rate from 0.774 to 0.792). Full report:
[`backend/eval/e2e/serving_detail.direct.md`](../backend/eval/e2e/serving_detail.direct.md).

| refusal | value | | answers (55 of 80) | value |
|---|---:|---|---|---:|
| false_accept_rate | 0.150 (3/20) | | supported claims (judge) | 0.792 (42/53) |
| answerable_refusal_rate | 0.133 (8/60) | | fully supported answers (judge) | 0.792 |
| refusal_accuracy | 0.850 | | citation resolves to a chunk | 0.818 (45/55) |
| context_hit_rate | 0.950 | | literal `[n]` placeholder | 0.145 (8/55) |

`serving_golden` is not reported: the GPU reached 96 C with 49 of its 90 cases
generated, and a partial suite is not a measurement.

> **These remain the baseline, not the current numbers.** The serving fixes that
> follow it were re-measured on 13 Oct 2026 - see
> [Re-measured after the serving fixes](#re-measured-after-the-serving-fixes-13-oct-2026).

**The model, not the gate, does most of the refusing.** Retrieval let 16 of the 20
held-out questions through (false_accept 0.800); end to end, 3 were answered
(0.150), so the model itself refused 13 of the 16. The same caution costs
answers: it refused 6 answerable questions that retrieval had accepted, 5 of them
with the right article in its context, and answerable refusal rises from 0.033
(2/60) at retrieval to 0.133 (8/60) end to end. The gate's weakness is being paid
for by a 3B model's reluctance, in both directions.

**Every fabrication came from a question that should have been refused.** The
judge found 11 of 53 claims unsupported. Each was read against its context:

| on reading | n | cases |
|---|---:|---|
| a fabrication - the context does not say it | 2 | `sd-u-006` gives the etymology of "oil" for a question about non-aromatic hydrocarbons; `sd-u-016` gives a Basque city the three rivers of Valdivia, Chile |
| stated by the context, but about something else | 1 | `sd-u-010` reports the AIM-54's 78 victories for a question about a different missile |
| correct and stated by the context; the judge missed it | 8 | 6 join facts from different sentences (`sd-a-014` takes "Donald J." from a chunk's first sentence and the 2021 revelation from its seventh; `sd-a-064` calls SCADE Suite a "model-based design tool", which the chunk says in its first sentence about the product's maker); 1 repeats the question's wording where the context uses other words ("killed" where the source says "involved in the murder"); 1 is a bare name |

So 0.792 is a floor: on reading, 51 of the 53 claims are stated by their context.
Only the flagged claims were read - the 42 the judge accepted were not - and
groundedness is not correctness, as `sd-u-010` shows. All three answers to
unanswerable questions were flagged. Two answers were not judged at all, because
a two-word name ("Clas Thunberg") is below the judge's minimum claim length.

The run's only fabrications therefore sit behind the refusal gate, which is where
step 3 aims: a relevance score that refuses those three questions before
generation removes them.

> **Step 3 is measured.** A cross-encoder relevance gate refuses all three, and
> end to end false accepts go 0.150 to 0.000 - at the cost of two more
> answerable refusals of 60 and 0.6 s of latency. The fit under-predicted that
> cost fourfold, which is what a held-out suite is for:
> [relevance-gate.md](relevance-gate.md).

**One answer in seven cited a placeholder.** The prompt said "Cite inline with [n]
markers", and in 8 of 55 answers the model copied `[n]` literally; two more cite
nothing. Such an answer reaches the UI with no source and nothing saying so. The
prompt is corrected in the serving changes that follow this baseline, and this
rate is the number that change has to move.

## Latency

Single requests against the running API, 2026-09-21:

| condition | total | where the time goes |
|---|---:|---|
| first request after a start | 75.4 s | 60.8 s loading the embedder (with an unauthenticated Hugging Face Hub check), 13.2 s loading the LLM |
| warm, GPU cool (1,425 MHz) | 2.9-4.9 s | generation ~98%; retrieval 45-75 ms |
| refused before retrieval (intent filter) | 24 ms | - |

Per answer in the end-to-end run on `serving_detail` (74 generations, client-side,
retrieval included):

| condition | n | p50 | min | max |
|---|---:|---:|---:|---:|
| clamped clock (median busy clock 210 MHz of 2,100 in 71, 348-555 MHz in 3) | 74 | 7.3 s | 3.1 s | 64.4 s |
| full clock | 0 | - | - | - |

**A clamped GPU costs about twice the cool time, not ten times.** Paced or not, the
card never stayed at full clock under sustained load - not one of the 74 samples
did - so 7.3 s is this laptop's sustained number and 2.9-4.9 s its best case. The
two slowest answers (64.4 s and 35.3 s) each came right after a cooldown pause
longer than five minutes (327 s and 394 s), which is Ollama's default keep-alive:
the model had been unloaded and was reloaded on a hot card, with the GPU busy in
only 2-4 of 13-18 polls. The 48 s mean in `engine_comparison.md` is therefore not
explained by the clamp alone; its run recorded no conditions to say more.

The `make bench-latency` run was made after the startup changes that followed this
baseline (models loaded at startup, the LLM kept resident), so it is reported with
them rather than here.

## Re-measured after the serving fixes (13 Oct 2026)

The baseline above ended with a number the serving changes had to move: `[n]`
copied literally into 8 of 55 answers. Re-measuring it needed 80 generations,
which on the reference laptop meant about ninety minutes of paced running, and
the thermal guard had already killed one attempt at 91 C. The run was moved to a
rented T4 instead: 80 cases in 3.5 minutes of generation plus 9 minutes of
judging, about $0.10, nothing to cool.

Commit `75b1c5d` (branch `feat/online-demo`, which contains the serving fixes),
Tesla T4, same corpus (`wikipedia`, 87,173 vectors) and same suite. Report:
[`backend/eval/e2e/2026-10-13-t4/serving_detail.direct.md`](../backend/eval/e2e/2026-10-13-t4/serving_detail.direct.md).

| | 21-22 Sep, laptop `2bcc858` | 13 Oct, T4 `75b1c5d` |
|---|---:|---:|
| literal `[n]` placeholder | 0.145 (8/55) | **0.000 (0/54)** |
| citation resolves to a chunk | 0.818 (45/55) | **1.000 (54/54)** |
| false_accept_rate | 0.150 (3/20) | 0.150 (3/20) |
| answerable_refusal_rate | 0.133 (8/60) | 0.150 (9/60) |
| refusal_accuracy | 0.850 | 0.850 |
| context_hit_rate | 0.950 | 0.950 |
| supported claims (judge) | 0.792 (42/53) | 0.812 (39/48) |
| answers carrying a flagged claim | 0.200 (11/55) | 0.167 (9/54) |
| answers with no judgeable claim | 2/55 | 6/54 |
| latency p50 | 7.3 s (74/74 clamped) | **1.79 s (74/74 full clock)** |

**The placeholder is gone, and no answer cites a number that resolves to
nothing.** Both were a UI problem, not a scoreboard problem: in September 10 of
55 answers reached the page with a citation marker that pointed at no chunk. Now
all 54 resolve, and `[n]` appears in none.

**The run is reproducible; the comparison is not a controlled experiment.** Two
full T4 runs at the same commit returned identical accuracy metrics - the same 9
answerable refusals, the same 3 false accepts, the same 9 flagged claims, the
same rates to four decimals - and differed only in latency (p50 1,768 vs 1,793
ms). So none of the right-hand column is run-to-run noise. But the two columns
differ in prompt, keep-alive, GPU, driver and date at once, so a change between
them cannot be attributed to the prompt alone.

**One more answerable question is refused, and three cases moved in both
directions.** `sd-a-009` was refused in September and is answered now;
`sd-a-039` and `sd-a-059` were answered and are refused now, both with the right
article in context. `sd-a-059`'s September answer was one of the placeholder
ones ("Bob Hancocks ... [n]"), so one of the two new refusals replaced an answer
a user could not have checked. Net 8 to 9 of 60. One case in 60 is not a
regression this run can demonstrate, and it is not free either: it is the number
to watch when step 3 changes the refusal gate.

**The supported-claim rate rose over a smaller base, which is less of a win than
it looks.** 0.792 (42/53) to 0.812 (39/48). Four more answers are now bare names
- "Hylopathism [1]", "Petrus Plancius [1]" - which fall below the judge's
minimum claim length and are therefore not judged at all: 6 of 54 answers versus
2 of 55. Counting whole answers instead, which claim length cannot flatter, 9 of
54 carry a flagged claim against 11 of 55. The three unanswerable questions that
were wrongly accepted are still flagged, as in September. A terser answer is
easier to support; a metric that improves because fewer sentences are judged has
improved less than its number says.

**Latency now describes the system rather than the cooling.** p50 1.79 s against
7.3 s, with every one of the 74 generations at full clock where the laptop had
none. The 72.2 s maximum is the first case, which pays the container's model
load; every other answer landed between 1.18 s and 2.60 s. The laptop's 7.3 s is
still the honest number for the laptop, and this is the honest number for a T4 -
each belongs to its hardware, which is why both are kept.

**Provenance.** The first cloud run reported `git_sha: null`: the image carries
the code without the `.git` that produced it, so the report named no commit -
the same hole `scored_git_sha` closed on the scoring side. `evaluate()` now
passes the sha in and both sides record it. The numbers above are the re-run,
not that first one.

```bash
modal run infra/modal/demo.py::evaluate --suite serving_detail --git-sha $(git rev-parse HEAD)
modal volume get rag-wikipedia-reports e2e/serving_detail.direct.json ./report.json
```

## Conditions and limits

- **Hardware.** RTX 3060 Laptop GPU (6 GB). With llama3.2:3b resident it idles at
  ~82 C, and one generation takes it to 88-92 C and a clamped clock. Unpaced, it
  reached 96 C after 33 generations. End-to-end runs are paced to start a
  generation only below 90 C; the accuracy metrics do not depend on the regime,
  and every latency sample records it. Since 13 Oct an end-to-end run can also
  be sent to a rented T4 (`infra/modal/demo.py::evaluate`), which needs no pacing
  - a cloud sample and a laptop sample are not comparable and are never mixed.
- **The judge.** `cross-encoder/nli-deberta-v3-base`, CPU, threshold 0.5, best
  premise over windows, sentences and adjacent pairs (see `eval/grounding.py` for
  the measurements behind that choice). "Unsupported by the context" is not
  "false".
- **Suite limits.** Absence is checked as a string, so a paraphrased answer would
  pass as absent; an answerable fact may also occur in another article, which
  scores as a miss if that article ranks first. Passages whose answer is the
  subject of another article were skipped for that reason.

## Reproduce

```bash
make eval-fixture && make eval-detail                            # fixture corpus
make eval-serving && make eval-serving-detail                    # served corpus
make e2e-serving ARGS="--pause-at 90 --resume-at 87"            # hours on a laptop GPU
make bench-latency                                               # needs the API up
```
