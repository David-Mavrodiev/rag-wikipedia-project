# 10-Minute Lesson — rehearsal script

**Title:** *Using a coding agent safely on a production RAG system*

For David's simulation and nonplusultra **Stage 3 (Live Delivery)**. The 3-minute
LinkedIn cut comes out of the 2:15–3:15 beat (the refusal story).

Companion docs: [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) (stack) ·
[MASTER_TRAINER_PREP_PLAN.md](MASTER_TRAINER_PREP_PLAN.md) (why this lesson) ·
[MASTER_TRAINER_RAG_CODEX_LAB.md](MASTER_TRAINER_RAG_CODEX_LAB.md) (the 90-min version)

---

## Pre-flight — T-15 minutes (do not skip)

```powershell
# 1) Stack up - from projects\rag-wikipedia
#    REDIS IS NOT OPTIONAL: rate limiting is on by default, and /query answers
#    503 when the limiter cannot reach it. Omitting it breaks the 0:45 beat.
docker compose up -d qdrant redis

# 2) Ollama (separate terminal). No env vars needed - the API sends its own
#    context window (LLM_NUM_CTX) with every request. Do NOT set
#    OLLAMA_NUM_GPU=0: it works, but it abandons the GPU and costs ~10x
#    latency on stage.
ollama serve

# 3) API + console - from projects\rag-wikipedia\backend (two more terminals)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m http.server 5500 --directory ..\demo

# 4) WARM IT. The first query pays the model load; warm is ~12 s
#    (measured, GPU, tiny corpus). Never let the room watch the cold one.
Invoke-RestMethod -Uri http://127.0.0.1:8000/query -Method Post `
  -ContentType 'application/json' `
  -Body '{"question":"Who was Abraham Lincoln?"}'
```

**Windows to pre-open, in order:** console tab · terminal for Codex · terminal for
pytest · `backend/app/core/vectorstore.py` in the editor.

**Run the Codex exercise once beforehand.** You need to know how long it takes and
what it produces. Never rehearse an unrun demo.

---

## Beat sheet

| Time | Say | Do |
|---|---|---|
| **0:00–0:45**<br>Objective | "By the end you'll know how to drive a coding agent through **inspect → plan → implement → verify** on a real codebase, and how to check its work. We'll do it on a running RAG system." | Nothing. Face the camera. **Objective inside 20 seconds.** |
| **0:45–2:15**<br>The system | "This answers questions from Wikipedia — but only from documents it retrieved, and it shows its sources." | Console → **"Who was Abraham Lincoln?"** → point at the green **✓ ancrée · 2 sources** badge and the citation chips. |
| **2:15–3:15**<br>Refusal = trust<br>*(LinkedIn cut)* | "The important half: when the documents don't support an answer, it says so." Then: "I had a bug where **correct** answers were labelled refusals, because the UI guessed from citation count. A correct answer shown as a failure destroys trust as fast as a hallucination." | Ask **"What did I have for breakfast this morning?"** → orange **refus contrôlé**, zero citations. |
| **3:15–3:45**<br>Set up the task | "Today's change: the vector store hardcodes the embedding dimension at 384. That blocks ever switching embedder. I'll have Codex make it configurable." | Show `vectorstore.py` line 11: `EXPECTED_DIM = 384`. |
| **3:45–4:30**<br>A. INSPECT | "I never start by asking for code. First I make it prove it understands the repo." | Paste prompt **A**. Read its answer aloud, briefly. |
| **4:30–5:30**<br>B. PLAN | "Still no code. I want the file list and what could break — that's my review checklist before a single line changes." | Paste prompt **B**. Point at the files it names. |
| **5:30–7:00**<br>C. IMPLEMENT | Narrate while it works: "Notice I scoped it — keep 384 as the default, don't touch retrieval or the API. Scope is how you keep an agent reviewable." | Paste prompt **C**. If it runs long, talk through the plan it produced. |
| **7:00–8:30**<br>VERIFY *(never cut)* | "This is the part people skip. I read the diff myself, then I run the tests." | `git diff` → read one hunk aloud.<br>`.\.venv\Scripts\python.exe -m pytest -q` → **296 passing**. |
| **8:30–9:15**<br>Judgment | "Last week the same agent added a metric that was correct — but it made the eval need the LLM, turning seconds into minutes. I kept the feature and put it behind a flag. **The agent accelerates; it doesn't absolve.**" | Optional: show `--with-groundedness` in `eval/run_eval.py`. |
| **9:15–10:00**<br>Close | "Four things: scope it, make it plan first, verify with tests, own the diff. And the dimension detail matters — bge is 384, OpenAI's small is 1536. Vectors of different sizes aren't interchangeable, so changing embedder means re-indexing. That's why migrations aren't free." | Stop talking. Don't trail off. |

---

## The four prompts (keep in a text file, paste don't type)

```text
A. "Read this repository and explain how the vector store decides the embedding
    dimension: what backend/app/core/vectorstore.py enforces, where the value comes
    from, and what happens today if the embedder produced a different size. Do not edit."

B. "EXPECTED_DIM is hardcoded to 384, which blocks swapping to a 1536-d or 3072-d
    embedder. Propose a plan to make it configurable via an EMBED_DIM environment
    variable, defaulting to 384, validated against a real embedding before the
    collection is created. List every file you would touch, the tests you would add,
    and what could break. Do not write code yet."

C. "Implement that plan. Keep the change minimal and keep 384 as the default so
    existing behaviour is unchanged. Do not touch retrieval, chunking, or the API."

D. "Run the backend test suite and ruff. Show me the results."
```

---

## If it breaks — say this, don't panic

| Situation | Line |
|---|---|
| Codex is slow | "While it works — notice what I asked for, and what I deliberately didn't." |
| Codex produces something wrong | **A gift.** "Good — this is exactly why you review. Look at what it changed that I didn't ask for." |
| A test fails | "Perfect. This is the loop working. The tests are the contract, and the contract just rejected the change." |
| Stack is dead | Open `DEMO_RUNBOOK.md` and diagnose out loud. That *is* criterion 4 being demonstrated, not a failure. |

A calm recovery scores **higher** than a clean run. Live troubleshooting is on the
assessment sheet; a flawless demo is not.

## Running long?

Cut the **8:30–9:15 judgment beat** — you can deliver it in Q&A. **Never cut the
verify beat**; verification is the entire point of the lesson.

## Two delivery rules

1. **Objective in the first 20 seconds.** Trainers who bury the outcome lose the room.
2. **Never narrate silence.** Anything over ~5 seconds of waiting gets talked over.

---

## Post-run checklist (for the debrief with David)

- [ ] Did I state the objective inside 20 seconds?
- [ ] Did I stay inside 10 minutes?
- [ ] Did I read the diff aloud rather than trusting the agent?
- [ ] Did I ever narrate silence?
- [ ] What did Codex get wrong, and did I catch it live?
- [ ] Ask him to grade me on his six criteria, and for feedback on **pace and clarity**
      specifically — not on the technical content.
