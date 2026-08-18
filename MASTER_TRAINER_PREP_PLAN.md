# Master Trainer Prep Plan — implementing David's feedback

Turns David Mavrodiev's mentoring feedback into an executable plan, mapped onto the
**nonplusultra / OpenAI Master Trainer** assessment (Stage 2: *Role Fit + Codex Lab
Assessment* with Matze; Stage 3: *Final Interview + Live Delivery*).

**The core insight:** you do not need to invent material. This repository already
contains real, unfixed findings, a real AI-review-rejection story, and six real
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

### Pick this feature: **add `groundedness` to the evaluation report**

`backend/eval/metrics.py` already defines `groundedness()` — and `run_eval.py`
**never calls it**. So the function is dead code, and the docs promise a metric the
report does not contain. That makes it an ideal exercise:

- **Real** — a genuine gap a reviewer found, not a toy task.
- **Scoped** — one metric, one report field, one test.
- **Verifiable** — `pytest` must stay green, and `report.json` gains a field.
- **Great teaching content** — "how do you know your RAG is any good?" is the
  question every enterprise asks. Evals are the most business-relevant topic you own.

**Backup features** (if you want a second run, or Codex finishes fast):
- Make `EXPECTED_DIM` env-configurable in `vectorstore.py` (currently hardcoded 384).
- Change `query()` from `async def` to `def` so blocking inference stops holding the
  event loop (see §4, drill 6 — one word, deep reasoning).

### The prompt sequence (do not skip step A)

```text
A. INSPECT — no edits yet
   "Read this repository and explain how the evaluation harness works: what
    backend/eval/run_eval.py measures, what backend/eval/metrics.py provides, and
    what ends up in report.json. Tell me what is defined but unused."

B. PLAN — still no edits
   "groundedness() exists in metrics.py but run_eval.py never calls it. Propose a
    plan to include it in the report for answerable cases. List every file you
    would touch, the tests you would add, and what could break. Do not write code yet."

C. IMPLEMENT — scoped
   "Implement that plan. Keep the change minimal. Do not modify the existing
    recall@k, MRR or refusal_accuracy logic, and do not add it to the pass/fail
    gate — report it only."

D. VERIFY
   "Run the backend test suite and ruff. Show me the results."
```

**Why step A matters in the room:** it demonstrates you drive the agent through
*inspect → plan → implement → verify* instead of prompting "add groundedness" and
hoping. That sequence *is* the teachable method.

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
1. Tests before and after (the suite is at **60 passing**).
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
| 0:00–1:00 | **Objective** | "By the end you'll know how to drive a coding agent through inspect → plan → implement → verify, and how to check its work. We'll add a quality metric to a real RAG evaluation." |
| 1:00–3:00 | **Explanation** | What the system does: retrieve → ground → cite → refuse. Why evals exist: an LLM answer is not automatically right, so you measure. Name the four metrics. |
| 3:00–6:30 | **Live demo** | Run the Codex sequence from §1. Narrate *why* you ask it to inspect and plan first. |
| 6:30–8:30 | **Validation** | Read the diff aloud. Run `pytest`. Run `make eval`. Show the new field in `report.json`. State what you'd reject. |
| 8:30–10:00 | **Conclusion** | The agent accelerates; it doesn't absolve. Recap: scope it, plan first, verify with tests, own the diff. |

**Rules for delivery**
- Say the objective in the first 20 seconds.
- Never narrate silence — if something takes 20 s, talk through what it's doing.
- Have a **pre-warmed terminal**; the first query on a cold model takes 13–69 s.
- If the demo breaks, that's §4 — narrate the diagnosis. A calm recovery scores
  *higher* than a clean run.

---

## 4. Failure drills — six real ones from this machine

David: *"a missing environment variable, an authentication error, a failed test,
malformed output, a rate limit, or an embedding-dimension mismatch."* You have hit
most of these for real. Learn the **symptom → cause → fix** triple for each.

| # | Symptom (real error) | Cause | Fix |
|---|---|---|---|
| 1 | `cudaMalloc failed: out of memory` → API returns **503 LLM unavailable** | Ollama loading the model onto a full GPU | `OLLAMA_NUM_GPU=0` (CPU) |
| 2 | `failed to allocate CPU buffer of size 12884901888` | 3B model advertises a **128k** context → 12.9 GB KV cache | `OLLAMA_CONTEXT_LENGTH=8192` — retrieval only sends ~3k tokens |
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
the older "OpenAI port second" framing above: the video feature is groundedness, and
the rest is enterprise hardening.

1. **Add groundedness to the eval report** - the live Codex/video feature. It turns
   a real unused metric into an enterprise-relevant quality signal, without changing
   the existing recall/refusal gates.
2. **Add GitHub Actions CI** - backend tests, frontend tests, lint, build, and RAG
   eval on every push or pull request.
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

> "Today we implemented step 1: a real RAG evaluation improvement. The remaining
> six steps are how I would harden this from a strong portfolio project into
> something closer to enterprise deployment quality."

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
- [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) — start/stop, warm-up, troubleshooting
- [CODEX_RUNBOOK.md](CODEX_RUNBOOK.md) — Codex CLI environment, the `codex_apps` startup fix, troubleshooting
- [MASTER_TRAINER_RAG_CODEX_LAB.md](MASTER_TRAINER_RAG_CODEX_LAB.md) — the lab itself
- [ANNOTATED_CODE.md](ANNOTATED_CODE.md) — every file line-commented (your accuracy proof)
- [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md) — rebuild guide; §17 = the dormant provider registry
