# Measurement baseline - before hybrid search and reranking

> **Status: point-in-time record, 21 Sep 2026.** The numbers every later change in
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

_Pending: `make e2e-serving` is running, paced at 90/87 C (see Conditions)._

## Latency

_Pending: `make bench-latency` runs after the end-to-end run, because two GPU
workloads at once would measure neither._

Already measured on 2026-09-21, single requests against the running API:

| condition | total | where the time goes |
|---|---:|---|
| first request after a start | 75.4 s | 60.8 s loading the embedder (with an unauthenticated Hugging Face Hub check), 13.2 s loading the LLM |
| warm, GPU cool (1,425 MHz) | 2.9-4.9 s | generation ~98%; retrieval 45-75 ms |
| refused before retrieval (intent filter) | 24 ms | - |

What a clamped GPU costs is NOT in this table yet. The wall-clock rate of an
unpaced run suggested ~20 s per generation at 210 MHz, but paced runs have
recorded 3-6 s generations under a 210 MHz reading, so the per-sample data from
the bench decides it rather than an inference from a rate.

## Conditions and limits

- **Hardware.** RTX 3060 Laptop GPU (6 GB). With llama3.2:3b resident it idles at
  ~82 C, and one generation takes it to 88-92 C and a clamped clock. Unpaced, it
  reached 96 C after 33 generations. End-to-end runs are paced to start a
  generation only below 90 C; the accuracy metrics do not depend on the regime,
  and every latency sample records it.
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
