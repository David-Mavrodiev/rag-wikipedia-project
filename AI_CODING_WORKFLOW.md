# Workflow for AI coding in this repository

Adapted from Matt Pocock's *Full Walkthrough: Workflow for AI Coding* (AI Engineer
Europe 2026) and applied to this repo. The talk's thesis: AI is not a new paradigm
that voids software engineering fundamentals — small tasks, shared understanding,
vertical slices, tight feedback loops — those fundamentals are exactly what makes
agents work.

Two constraints drive everything below.

---

## Constraint 1 — the smart zone and the dumb zone

Attention relationships between tokens scale quadratically, so an agent does its
best work at the *start* of a context window and gets measurably dumber as the
window fills. The marker is roughly **100k tokens**, and it does not move when the
context window gets bigger: a 1M window is not a bigger smart zone, it is more dumb
zone. A big window is good for *retrieval*; it is not good for *coding*.

Consequences for this repo:

- **Size every task to fit in the smart zone.** If a task cannot plausibly be done
  in one window, it is not a task yet — it is a plan that needs splitting.
- **Watch the token counter.** Knowing the exact live token count is not a nicety;
  it is how you know how close you are to the dumb zone. Configure a status line
  that shows it.
- **The big docs in this repo are context bombs.** [PROJECT_BLUEPRINT.md](PROJECT_BLUEPRINT.md),
  [ANNOTATED_CODE.md](ANNOTATED_CODE.md) and [TESTS_ANNOTATED.md](TESTS_ANNOTATED.md)
  are tens of thousands of tokens each. Reading two of them whole spends half the
  smart zone before any work starts. Never read them end to end — grep for the
  section, or delegate the reading to a sub-agent and take back the summary.
- **Use sub-agents as delegation.** A sub-agent has its own isolated context; it can
  burn 90k tokens exploring and report back a few hundred. Exploration of an
  unfamiliar part of `backend/` should be delegated, not done inline.
- **Keep the always-on context tiny.** Everything in the system prompt — `CLAUDE.md`,
  skills, MCP tool definitions — is paid for on every single turn.

> **Open item:** the `CLAUDE.md` that Claude Code currently picks up here lives in the
> *parent* directory (`Downloads/CLAUDE.md`) and describes a different product (the
> Sentinel monitoring SaaS). Every session in this repo starts polluted with another
> project's conventions. Either add a small `CLAUDE.md` at this repo's root or move
> the Sentinel one into its own directory.

---

## Constraint 2 — the agent is the guy from *Memento*

Every session resets to the same blank state. That is a feature, not a bug.

**Prefer `/clear` over `/compact`.** Compacting looks thrifty but it leaves sediment:
a lossy, non-deterministic summary of a session that had already drifted into the dumb
zone, which you then build on top of. Clearing returns you to a state that is *always
the same*, so it can be optimised for. The way to make clearing cheap is to keep the
durable knowledge in files — a PRD, an issue, a test, a runbook — not in conversation
history.

**Corollary:** if losing your context window would be painful, you are storing something
important in the wrong place. Write it down, then clear.

---

## The pipeline

```text
brief ──▶ 1. GRILL ──▶ 2. PRD ──▶ 3. SLICE ──▶ 4. IMPLEMENT ──▶ 5. VERIFY
          (human)      (asset)    (human)       (AFK)            (gates)
          └──────── shared understanding ───────┘
```

### Phase 1 — Grill (human in the loop, always)

Before any plan, before any code: have the agent interview you relentlessly about the
change, one question at a time, each with its own recommended answer, until you and it
have the same picture. Frederick P. Brooks calls this the **design concept** — the idea
shared by everyone building the thing. That is the actual goal here. Not a document.

Expect 20–80 questions. Expect several you had not thought of — the value is in the
questions you would not have asked yourself, the ones that decide whether the feature
is right or subtly wrong.

The prompt is short enough to keep in your head:

> *Interview me relentlessly about every aspect of this plan until we reach a shared
> understanding. Walk down each branch of the design tree, resolving dependencies one
> by one. For each question, provide your recommended answer. Ask the questions one at
> a time.*

Grilling also works on inputs from the world: paste a meeting transcript, a bug report,
a review comment, and grill through the assumptions it left implicit.

**This phase cannot be automated.** It is the one place where a human must be present.
Everything downstream can be handed off; alignment cannot.

Grill-worthy questions in this repo look like: *should this number be a gate or a
report?* — *does this change what `audit_report.json` means?* — *does the held-out slice
still hold if we do this?* — *is this a retrieval decision or a generation decision?*

### Phase 2 — PRD, the destination document

Summarise the grilling session into a document that describes **where we are going**:
problem statement, solution, user stories, implementation decisions, testing decisions,
and — importantly — **the list of modules you expect to touch**. Naming the modules up
front is what keeps this from becoming spec-driven vibe coding: the code stays in view
the whole time.

Pocock's stance is that you don't need to re-read the PRD if the grilling was real —
you already share the design concept, so reading it only tests the model's ability to
summarise, which is the one thing models are reliably good at. Reasonable. But *do*
read the "modules to touch" and "testing decisions" sections: those are design claims,
not summary, and in this repo a wrong one costs an ingestion run.

Write PRDs to `docs/prd/` (or GitHub issues) — not into the chat.

### Phase 3 — Slice (human in the loop)

Do **not** ask for a linear multi-phase plan. Ask for a **Kanban board**: a set of
issues with explicit blocking relationships, each tagged `AFK` or `human-in-the-loop`.
It is cheap to produce once the PRD exists, and it makes the dependency structure
visible where a phase list hides it.

Then fix the one thing the agent reliably gets wrong.

**Agents slice horizontally. Slice vertically.** Left to itself, an agent plans "phase 1:
all the schema, phase 2: all the API, phase 3: the frontend" — three phases, nothing
working until the last one, and every phase's mistakes discovered at the end. The
Pragmatic Programmer's **tracer bullet** is the fix: cut a thin slice through *every*
layer that produces an observable result, then widen it.

In this stack the layers are `pipeline → Qdrant → retrieval → API → frontend`, plus
`eval`. A vertical slice runs all the way from ingest to a number in
`audit_report.json`:

| ✗ horizontal | ✓ vertical |
|---|---|
| "Add all chunking changes" | "One chunking variant, ingested into the 150-article fixture, measured by `make eval-fixture`" |
| "Build the coverage-gate infra" | "Coverage gate at one threshold, swept on the fixture, delta posted" |
| "Rewrite the frontend components" | "One citation-rendering change, its Vitest test, visible in the demo" |

A slice that cannot end in a measurement is not a slice.

### Phase 4 — Implement (AFK)

Once a slice is written down with its verification criteria, implementation is an
away-from-keyboard task. Give it three things: the issue, the modules, and the command
that proves it worked. Clear the context between slices.

**A task is only AFK if it has a gate.** In this repo, the gates already exist:

```bash
make lint             # ruff + eslint
make test             # pytest + vitest
make docs-check       # factual claims in the docs vs the repository
make audit-freshness  # has anything moved the metrics since the audit ran?
make eval-fixture     # metrics on the committed 150-article fixture
make sweep-coverage   # threshold sweep
```

If the change you are handing off cannot be checked by one of these, either add the
check first or keep the task human-in-the-loop. No gate, no AFK.

### Phase 5 — Verify

The repo's own thesis applies to agent output more than to anything else: **do not fool
yourself with your own metrics.** Two standing rules:

1. **Never let an agent relax a gate to make it pass.** `false_accept_rate` is red on
   purpose. An agent that "fixes" a failing gate by moving the threshold has destroyed
   the most valuable thing in the repository. If a gate goes green, ask what changed
   before celebrating.
2. **A green test run is not a green feature.** Agents optimise for the loop you give
   them. Run the demo ([DEMO_RUNBOOK.md](DEMO_RUNBOOK.md)) on anything that touches
   retrieval or generation.

---

## Human in the loop vs AFK

The most useful triage question before starting anything: *can I walk away from this?*

| human in the loop | AFK |
|---|---|
| Choosing the IDF coverage-gate threshold (a judgement about honesty, not a number to optimise) | Wiring the gate into the audit once the threshold is decided |
| Deciding whether a metric is a gate or a report | Adding a suite that measures it |
| Anything that changes what a published number *means* | Anything with a `make` target that proves it |
| Schema / corpus / ingestion decisions (expensive to undo — see the four shutdowns) | Refactors covered by the existing suites |
| Deciding that a red gate stays red | Documenting why it is red |

Crucial decisions want *more* humans, not fewer — pair or mob with the agent as a third
participant that quizzes everyone relentlessly. Implementation wants fewer.

---

## Anti-patterns

| Don't | Why |
|---|---|
| Spec-to-code: edit the spec, regenerate, never read the code | Vibe coding with extra steps. The code is the battleground; you have to keep a handle on it. |
| `/compact` and keep going | Sediment. Clear instead, and rely on the written artefacts. |
| Adopt a planning framework wholesale (spec-kit, taskmaster, …) | There is no clear winner yet. Own your planning stack, or you can't debug it when it breaks. |
| Let the agent plan the layers | It will go horizontal every time. |
| Hand off a task with no gate | You'll review the diff line by line anyway — that isn't AFK. |
| Fill the context because the window is large | The window grew; the smart zone didn't. |
| Blame the model for bad output in a bad module | Bad codebases make bad agents. Garbage in the module, garbage out of the agent. |

---

## Session checklist

**Starting**

- [ ] `/clear` — start from the deterministic base state
- [ ] Am I aligned, or do I need to grill first?
- [ ] Is there a written destination (PRD / issue) for this?
- [ ] Is the slice vertical, and does it end in a measurement?
- [ ] Do I know the command that proves it worked?

**During**

- [ ] Token count in view, below ~100k
- [ ] Big docs grepped or delegated, never read whole
- [ ] Exploration of unfamiliar code delegated to a sub-agent

**Finishing**

- [ ] `make lint && make test`
- [ ] `make docs-check` if any documented fact moved
- [ ] `make audit-freshness` if anything that moves the metrics changed
- [ ] Gates read honestly — nothing relaxed to green
- [ ] Durable knowledge written to a file, then `/clear`

---

## Appendix — skills to add

None of these exist yet in this repo. Each is deliberately tiny; the point is that you
own them and can change them when they misbehave.

`.claude/skills/grill-me/SKILL.md`

```markdown
---
name: grill-me
description: Interview the user relentlessly about a plan until shared understanding is reached. Use before writing a PRD or any non-trivial change.
---

Interview me relentlessly about every aspect of this plan until we reach a shared
understanding. Walk down each branch of the design tree, resolving dependencies one
by one. For each question, provide your recommended answer. Ask the questions one at
a time, and wait for my response before asking the next.

Explore the repository first, and delegate that exploration to a sub-agent so this
conversation stays in the smart zone. Do not produce a plan and do not write code.
Alignment is the only goal.
```

`.claude/skills/write-prd/SKILL.md`

```markdown
---
name: write-prd
description: Turn an alignment conversation into a PRD — the destination document. Use after grill-me.
---

Write a PRD to docs/prd/<slug>.md with these sections:

1. Problem statement — the problem the user actually has
2. Solution — one paragraph
3. User stories
4. Modules to touch — propose the list and confirm it with me before writing
5. Implementation decisions — every decision reached in the alignment conversation
6. Testing decisions — how each story is verified, naming the make target

Confirm the module list with me first. Everything else, write without asking.
```

`.claude/skills/slice/SKILL.md`

```markdown
---
name: slice
description: Split a PRD into a Kanban board of vertically-sliced issues with blocking relationships.
---

Split the PRD into issues under docs/issues/. For each one: title, what it changes, the
command that proves it works, what blocks it, and a type of AFK or HUMAN-IN-THE-LOOP.

Slice vertically, not by layer. Every issue must cut through all the layers it needs
(pipeline → retrieval → API → frontend → eval) and end in an observable result: a
passing test, a metric in audit_report.json, something visible in the demo. Never
propose an issue whose title is a layer name.

An issue with no verifying command is HUMAN-IN-THE-LOOP by definition.
```
