# rag-wikipedia-project

A retrieval-augmented generation system over Wikipedia, built without a RAG
framework so that every decision in the chain — chunking, retrieval, the
retrieval→prompt contract, refusal, evaluation — is explicit and testable.

The code is in **[projects/rag-wikipedia/](projects/rag-wikipedia/)**. Start with
its [README](projects/rag-wikipedia/README.md) to run it.

```
Browser → FastAPI → [ bge-small-en-v1.5 → Qdrant → llama3.2:3b ] → cited answer
Prefect pipeline:  Wikipedia → clean → chunk → embed → Qdrant
```

Qdrant for vector search, Ollama for generation, FastAPI backend, React + Vite
frontend, Prefect for the ingestion flow. Everything runs locally.

## What is actually built

| | |
|---|---|
| Corpus served | **24,694 articles / 87,173 vectors** (full 25k profile) |
| Tests | **285 backend tests** (pytest) + **11 frontend tests** (Vitest) |
| CI | lint, tests, documentation checks, and a quality delta posted on every PR |
| Quality gates | four, all unconditional — and **both refusal gates are red on purpose** |

## The part worth reading

Most of the engineering in this repository is about **not fooling yourself with
your own metrics**. Three examples, each of which began as a wrong number that
looked right:

**The eval suites scored 1.000 while the demo refused.** The metrics measured the
*retrieval* decision, not the generated answer, and the expectations were matched
as substrings — so "What is the speed of light?" scored a perfect recall against a
corpus with no such article. The suites were rebaselined onto honest expectations
and the gates went red. They are still red. `false_accept_rate` is 0.600 against a
0.10 gate, and the report says `suspect_overfit` rather than being relaxed to pass.

**The gate gets weaker as the corpus grows.** `false_accept_rate` measured at three
corpus sizes rises monotonically — 0.450 at 60 articles, 0.500 at 500, 0.600 at
24,694 — because accepting on a single shared token means every additional article
is another chance for an irrelevant chunk to supply one. Retrieval scaled fine over
the same 165× increase (recall@5 1.000 → 0.933). Refusal did not. That is a design
defect, documented as one, with a threshold sweep recorded showing why tuning does
not fix it.

**A 7-second benchmark measured 69.8 chunks/s; sustained throughput was 1–24.**
The benchmark ran on a cold GPU. Under sustained load the card thermally clamps to
210 MHz of 2,100 and the machine shut down four times. Separately, extrapolating chunks-per-article from the first
150 articles of the stream overestimated the final vector count by 4.6× — Wikipedia
dumps front-load their long articles. Both corrections are written up in
[docs/ingestion-performance.md](projects/rag-wikipedia/docs/ingestion-performance.md),
above the predictions they replaced.

What made that 25k ingestion finish was not speed. It was **resumability**:
deterministic point IDs plus an existence check per segment meant four shutdowns
cost zero re-embedded chunks.

## Guarding against the same mistakes

The measurement infrastructure is the point, not a side-effect:

- **Provenance.** `audit_report.json` records the git sha, profile, collection and
  vector count it measured. `GET /quality` *refuses* to report those numbers when
  the deployment serves a different collection.
- **Staleness.** `make audit-freshness` fails when any file that could move the
  metrics has changed since the report was generated.
- **Documentation.** `make docs-check` verifies this repository's factual claims
  against the repository itself — test counts, file counts, suite sizes, settings,
  compose services. It exists because the test count was once documented six
  different ways across seven files.
- **Held-out slice.** 1 article in 100 (`sha256(article_id) % 100 == 0`) is never
  ingested, so out-of-corpus eval cases stay valid at any corpus size instead of
  expiring when the corpus grows.
- **PR deltas.** Every pull request ingests the committed 150-article fixture,
  re-runs the audit, and posts the metric delta against the committed baseline.

## Map

| Path | What |
|---|---|
| [projects/rag-wikipedia/](projects/rag-wikipedia/) | the system — backend, frontend, pipeline, evals |
| [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) | how to run a live demo, with the queries re-verified against the current index |
| [TEST_COVERAGE_SUMMARY.md](TEST_COVERAGE_SUMMARY.md) | what is tested, what is not, and the measured quality numbers |
| [TESTS_ANNOTATED.md](TESTS_ANNOTATED.md) | per-test annotations — what each test proves |
| [ANNOTATED_CODE.md](ANNOTATED_CODE.md) | annotated walkthrough of the implementation |
| [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md) | design decisions and their rationale |
| [docs/ingestion-performance.md](projects/rag-wikipedia/docs/ingestion-performance.md) | the performance analysis, and its own correction |
| [docs/runbook.md](projects/rag-wikipedia/docs/runbook.md) | operational runbook |

Documents marked **point-in-time record** are dated snapshots kept as evidence of
what was true then; where they disagree with the repository, the repository is
right.
