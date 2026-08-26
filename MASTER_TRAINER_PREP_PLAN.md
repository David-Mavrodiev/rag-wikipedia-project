# Master Trainer Prep Plan — implementing David's feedback

Turns David Mavrodiev's mentoring feedback into an executable plan, mapped onto the
**nonplusultra / OpenAI Master Trainer** assessment (Stage 2: *Role Fit + Codex Lab
Assessment* with Matze; Stage 3: *Final Interview + Live Delivery*).

**The core insight:** you do not need to invent material. This repository already
contains one real open finding, a real AI-review-rejection story, and six real
failures with real error messages. Use them.

---

## 0. What they are actually assessing

David's six criteria, and where your evidence already lives:

| # | They want to see… | Your existing evidence |
|---|---|---|
| 1 | You understand the tech well enough to **explain it accurately** | `ANNOTATED_CODE.md` — you line-commented the entire codebase |
| 2 | You can **use Codex effectively** on a real codebase | This repo + the scoped feature in §1 below |
| 3 | You **review and validate** its work instead of accepting blindly | §2 — you already rejected two AI recommendations, with reasons |
| 4 | You **troubleshoot calmly** when code/tests/environment fail | §4 — six real failures, real error text, real fixes |
| 5 | You **adapt the explanation** for engineers vs business | §5 — two-level scripts |
| 6 | You can **lead a structured session** with confidence | §3 — the 10-minute lesson |

> Criterion 3 is the one most candidates fail. Anyone can run an agent. Far fewer
> can say *"here is what it got wrong, and here is how I proved it."*

---

## 1. The Codex exercise — one small, real feature

David: *"Ask it to inspect the repository first, produce a plan, implement a scoped
change, and run the relevant checks."*

### Already done — the groundedness exercise (use this as your case study)

This exercise has been **run and shipped**, so treat it as finished material rather
than something to demo live. What happened, end to end:

| Step | What happened |
|---|---|
| **Inspect / plan** | Codex read the eval harness and found `groundedness()` in `metrics.py` was never called by `run_eval.py` — defined but dead. |
| **Implement** | It split `main()` into `validate_golden_set` / `evaluate_golden` / `gate_failures` (making the harness unit-testable for the first time), added groundedness to `report.json`, and correctly kept it **out** of the pass/fail gate. → commit `8b595e5` |
| **Review — the part that matters** | Accepted, but with a real cost identified: measuring groundedness needs **one LLM call per answerable case**, which turned a seconds-long retrieval-only eval into a minutes-long run *and* added a hard dependency on the LLM being reachable. `make eval` is a documented command, so that is a genuine regression in operability. |
| **Fix** | Scoped the cost behind `--with-groundedness`. Default stays fast and LLM-free; `make eval-groundedness` opts in. When not measured the key is **omitted** from the report rather than reported as `0.0` — "not measured" must not read as "badly grounded". → commit `a9aac36` |

**Why this is your strongest answer to David's criterion 3.** You did not simply
accept the agent's work, and you did not reject it either — you *kept the feature and
scoped its cost*. That is a harder, more senior move than a thumbs up or down.

> The line: *"It worked and the tests were good. What I flagged in review was that it
> turned a retrieval-only harness into one that needs the LLM — eval went from seconds
> to minutes and would fail if Ollama was down. So I kept the metric and put it behind
> a flag."*

### For the live demo — make `EXPECTED_DIM` configurable

Pick an **unimplemented** feature so the demo is real. This one is ideal:

`backend/app/core/vectorstore.py` hardcodes `EXPECTED_DIM = 384` and raises if a
different dimension is passed. That blocks any embedder swap, and it is the exact
failure David told you to rehearse — **embedding-dimension mismatch**.

- **Real** — an open CodeRabbit finding, not a toy task.
- **Scoped** — one constant, one validation, one test.
- **Verifiable** — `pytest` stays green; the default (384) must not change.
- **Accepted only when proven** — the implementation must show where `EMBED_DIM` is
  read, keep the default collection shape unchanged, and reject a real embedding whose
  length does not match the configured dimension.
- **Great teaching content** — bge-small is 384-d, `text-embedding-3-small` is 1536,
  `text-embedding-3-large` is 3072. Vectors of different dimensions are **not**
  interchangeable, so switching embedder forces a **re-ingest into a new collection**.
  That single fact explains why RAG migrations are not free.

### The prompt sequence (do not skip step A)

```text
A. INSPECT — no edits yet
   "Read this repository and explain how the vector store decides the embedding
    dimension: what backend/app/core/vectorstore.py enforces, where the value comes
    from, and what happens today if the embedder produced a different size."

B. PLAN — still no edits
   "EXPECTED_DIM is hardcoded to 384, which blocks swapping to a 1536-d or 3072-d
    embedder. Propose a plan to make it configurable via an EMBED_DIM environment
    variable, defaulting to 384, and to validate it against a real embedding before
    the collection is created. List every file you would touch, the tests you would
    add, and what could break. Do not write code yet."

C. IMPLEMENT — scoped
   "Implement that plan. Keep the change minimal and keep 384 as the default so
    existing behaviour is unchanged. Do not touch retrieval, chunking, or the API."

D. VERIFY
   "Run the backend test suite and ruff. Show me the results."
```

**Why step A matters in the room:** it shows you drive the agent through
*inspect → plan → implement → verify* rather than prompting "make it configurable"
and hoping. That sequence *is* the teachable method.

**Backup features** (if Codex finishes fast, or you want a second run):
- Strict golden-set validation in `run_eval.py` — `expected_refusal` is currently
  classified by truthiness, so the string `"false"` is silently treated as
  *unanswerable*.
- Change `query()` from `async def` to `def` so blocking inference stops holding the
  event loop (see §4, drill 6). One keyword, deep reasoning — better *explained* than
  demoed.

### Record as you go (you will need this for §2)
- The plan Codex produced — did it find every file?
- Anything it changed that you did **not** ask for.
- Whether it ran the checks or only claimed to.
- Each point where you intervened.

---

## 2. Review and validate — the criterion that separates you

David: *"be ready to explain what Codex did correctly, what you changed or rejected,
and how you verified the result."*

### You already have two true stories. Rehearse them.

**Story A — you rejected an AI reviewer's recommendation, with evidence.**
CodeRabbit flagged the `Makefile` as broken: `make ingest`/`make eval` supposedly ran
outside the backend environment. It hedged — *"unless a root project or workspace
exists."* One does: `projects/rag-wikipedia/pyproject.toml` declares
`[tool.uv.workspace] members = ["backend"]`. You tested both forms and each resolved
the same interpreter, so you **reverted your own change** and documented why.

> The line: *"The reviewer was confident and wrong. I checked before I acted, and
> then I left a comment so nobody 'fixes' it again."*

**Story B — you rejected a recommendation that would have reintroduced a bug.**
CodeRabbit asked you to *reject any generated answer without a citation*. That
directly contradicts a bug you had just fixed: the 3B model intermittently returns a
**correct, grounded** answer with no `[n]` markers. Treating those as failures is
exactly the mislabeling you eliminated. You declined and noted the reasoning — and if
stricter behavior were ever wanted, the right design is a separate `cited` field, not
conflating *uncited* with *refused*.

> The line: *"A reviewer optimizing for one property can break another. I kept the
> user-facing contract."*

**How you verify — the standard answer:**
1. Tests before and after (the suite is at **199 passing**).
2. A new regression test for the specific case.
3. Lint (ruff) not worse than baseline.
4. A live end-to-end run, not just green tests.

---

## 3. The 10-minute lesson

David: *"a clear objective, a short explanation, a live demonstration, a validation
step, and a conclusion."* This is also what you will deliver to him in the simulation.

**Title:** *Using a coding agent safely on a production RAG system*

| Time | Segment | Content |
|---|---|---|
| 0:00–1:00 | **Objective** | "By the end you'll know how to drive a coding agent through inspect → plan → implement → verify, and how to check its work. We'll make the vector-store embedding dimension configurable without changing the default behavior." |
| 1:00–3:00 | **Explanation** | What the system does: retrieve → ground → cite → refuse. Explain why vector dimensions are a storage contract: a 384-d collection cannot safely accept 1536-d or 3072-d embeddings. |
| 3:00–6:30 | **Live demo** | Run the Codex sequence from §1. Narrate *why* you ask it to inspect and plan first. |
| 6:30–8:30 | **Validation** | Read the diff aloud. Run `pytest` and ruff. Show that 384 remains the default and that a mismatched embedding dimension fails before collection creation. State what you'd reject. |
| 8:30–10:00 | **Conclusion** | The agent accelerates; it doesn't absolve. Recap: scope it, plan first, verify with tests, own the diff. |

**Rules for delivery**
- Say the objective in the first 20 seconds.
- Never narrate silence — if something takes 20 s, talk through what it's doing.
- Have a **pre-warmed terminal**; the first query pays the model load, warm is ~12 s.
- If the demo breaks, that's §4 — narrate the diagnosis. A calm recovery scores
  *higher* than a clean run.

---

## 4. Failure drills — six real ones from this machine

David: *"a missing environment variable, an authentication error, a failed test,
malformed output, a rate limit, or an embedding-dimension mismatch."* You have hit
most of these for real. Learn the **symptom → cause → fix** triple for each.

| # | Symptom (real error) | Cause | Fix |
|---|---|---|---|
| 1 | `cudaMalloc failed: out of memory` → API returns **503 LLM unavailable** | Ollama sizing CUDA buffers for the model's **128k** context; the compute buffer will not fit a 6 GB card | The app sends `num_ctx` itself (`LLM_NUM_CTX`, default 8192). Bounded context is the fix — **not** `OLLAMA_NUM_GPU=0`, which avoids it only by abandoning the GPU at ~10x latency |
| 2 | `failed to allocate CPU buffer of size 12884901888` | Same cause on the CPU path: a 12.9 GB KV cache for 128k context | Same fix. Retrieval only ever sends ~3k tokens (`token_budget`) |
| 3 | Qdrant exits **101**: ``unknown variant `on_disk` `` | Server v1.18.3 reading storage written by v1.9.2 | Fresh volume + re-ingest; version skips are unsupported |
| 4 | **503 Vector store unavailable**, log: `'QdrantClient' object has no attribute 'query_points'` | Client pinned `<1.10` while the code calls the Query API | Pin `qdrant-client>=1.12`; the pin and the call site are **one decision** |
| 5 | `bind: Only one usage of each socket address` | Ollama tray app already holds :11434 in GPU mode | Kill `ollama*`, restart with CPU env vars |
| 6 | Two users, one blocks the other | `async def` endpoint doing **blocking** inference on the event loop | Make it `def` — FastAPI runs sync endpoints in a threadpool |

**The two David named that you should stage deliberately:**

- **Embedding-dimension mismatch** — `vectorstore.py` has `EXPECTED_DIM = 384` and
  raises on mismatch. Explain: bge-small is 384-d, `text-embedding-3-small` is 1536,
  `3-large` is 3072. Vectors of different dimensions are not interchangeable, so
  switching embedder means **re-ingesting into a new collection**. Point at
  `providers.py`, where this is already documented.
- **Auth error / missing env var** — with an OpenAI key set, `401` = key not present
  in *this* shell (the classic: set in another terminal), `429` = rate/quota → batch
  and back off.

**The framing that scores:** *"In a live lab you are not debugging your own code, you
are unblocking twenty people fast. The skill is recognition, not depth."*

---

## 5. Two-level explanations

Practise each **out loud**, 60 seconds per level.

**Codex**
- *Engineer:* A terminal-first coding agent. It reads the repo, proposes multi-file
  changes, and runs commands in a sandbox behind an approval workflow. Supports MCP.
- *Business:* A developer assistant that works on the actual codebase rather than
  suggesting snippets — it drafts the change and a human approves it.

**RAG**
- *Engineer:* Chunk, embed, store vectors, retrieve top-k by cosine, apply a score
  threshold and token budget, build a grounded prompt with `[n]` markers, parse
  citations back to sources.
- *Business:* The model doesn't reliably know your internal documents. So we look up
  the relevant passages first and require the answer to cite them — and to say
  "I don't know" when the documents don't support one.

**Agents / tool calling**
- *Engineer:* The model returns a structured call; your code executes it and feeds
  the result back; loop until done. You own the tools and the stopping condition.
- *Business:* It can *do* things, not just talk — look something up, file a ticket —
  and you decide exactly which actions it's allowed to take.

**Evals**
- *Engineer:* A golden set of question→expected-keyword pairs; recall@k and MRR for
  retrieval; refusal accuracy on deliberately unanswerable questions; a 0.8 gate.
- *Business:* A regression test for answer quality — so an upgrade that silently
  makes answers worse gets caught before customers see it.

---

## 6. Priority call: Codex first, OpenAI port second

David is right, and it matches the posting: Stage 2 is explicitly a **Codex Lab
Assessment**. Order:

1. **Codex exercise + the 10-minute lesson** (§1–§3) — the assessed skill.
2. **Failure drills** (§4) — cheap, high return, and you have the real material.
3. **Two-level explanations** (§5) — pure rehearsal.
4. **OpenAI API port** — do it only if 1–3 are solid. It is genuine OpenAI-stack
   experience and the dormant registry in `providers.py` makes it a short job, but it
   is not what Stage 2 tests.

---

### Updated seven-step "what to do next" plan

This is the current plan for the recorded video and follow-up roadmap. It supersedes
the older "OpenAI port second" framing above: the rest is enterprise hardening.

1. ~~Add groundedness to the eval report~~ - **DONE** (commits `8b595e5`,
   `a9aac36`). Shipped, then scoped behind `--with-groundedness` after review.
   Use it as the case study in section 1; the **live** Codex/video feature is now
   making `EXPECTED_DIM` configurable, which is still open.
2. **Extend GitHub Actions CI** - backend lint/tests now run on push and pull
   request; add frontend tests/build and the RAG eval gate next.
3. **Add deployment smoke tests and rollback automation** - deploy a new Azure
   Container Apps revision, verify health/query/refusal/frontend, then shift traffic
   or roll back.
4. **Add authentication and authorisation** - protect public access before calling
   the app internet-ready; keep internal services private.
5. **Add API reliability controls** - request IDs, structured errors, timeouts,
   readiness checks, and preserved refusal semantics.
6. **Add load testing** - measure `/query` p50/p95 latency, error rate, and refusal
   behavior under concurrent users.
7. **Add production secret, dependency, and typing hardening** - Key Vault or managed
   identity, dependency scanning, frontend lockfile enforcement, and backend static
   type checks.

Video conclusion line:

> "Today we implemented the next live Codex exercise: making the embedding-dimension
> contract configurable while preserving the 384-d default. The groundedness metric
> is the review case study; the remaining roadmap is how I would harden this from a
> strong portfolio project into something closer to enterprise deployment quality."

## 7. For the simulation with David

He proposed delivering the 10-minute lesson to him instead of more theory. Before that call:

- [ ] Run the Codex exercise **end to end at least once** — never rehearse an unrun demo.
- [ ] Write down: what Codex got right, what you changed, what you rejected, how you verified.
- [ ] Time the lesson. Ten minutes is shorter than it feels; cut content, not the demo.
- [ ] Pre-warm the stack (`DEMO_RUNBOOK.md`) and pre-open every window you need.
- [ ] Ask him to interrupt with a hostile question and to break something mid-demo.
- [ ] Ask specifically for feedback on **pace and clarity**, not on the technical content.

**Ask him to grade you on David's own six criteria** — that turns a friendly rehearsal
into a real dry run.

---

## Related docs
- [MASTER_TRAINER_LESSON_SCRIPT.md](MASTER_TRAINER_LESSON_SCRIPT.md) - beat-by-beat rehearsal script for the 10-minute lesson (section 3)
- [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) — start/stop, warm-up, troubleshooting
- [CODEX_RUNBOOK.md](CODEX_RUNBOOK.md) — Codex CLI environment, the `codex_apps` startup fix, troubleshooting
- [MASTER_TRAINER_RAG_CODEX_LAB.md](MASTER_TRAINER_RAG_CODEX_LAB.md) — the lab itself
- [ANNOTATED_CODE.md](ANNOTATED_CODE.md) — every file line-commented (your accuracy proof)
- [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md) — rebuild guide; §17 = the dormant provider registry
