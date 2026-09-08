# Verifying review findings against a running system

*Written 2026-08-29. Method note, and the record of one session.*

An automated reviewer gave this repository 154 comments across 10 reviews. I
grouped them into tiers and started fixing. Partway through I stopped and did
something different: I brought the stack up and tried to **reproduce** the
findings instead of reading them.

Two of my own severity calls were wrong — in opposite directions. That is the
reason this document exists. A backlog you have only read is a list of
*claims*; the ones you have reproduced are a list of *facts*, and the gap
between them is not small.

## The rule

> Listing is not testing. A declared behaviour can still fail on contact.

The corollary that costs the most: **a finding you filed yourself gets no more
credit than one a tool filed.** Both are claims until something runs.

---

## Finding 1 — downgraded by the evidence

**My filing.** `validate_golden_set` raises `SystemExit` from a library
function reachable through `POST /quality/audit`. `SystemExit` inherits from
`BaseException`, so it bypasses Starlette's exception middleware. I wrote it up
as *"the last remaining way to crash the audit endpoint"* and put it at the top
of the tier.

**What actually happened.** I truncated `golden.jsonl` below `MIN_CASES = 20`
and posted the request. The endpoint returned `500 Internal Server Error` with
an empty body, the log showed the `SystemExit`, and `GET /health` answered
`200` immediately afterwards. **The worker did not die.**

**Why I was wrong.** The handler runs the audit through
`run_in_threadpool`. The `SystemExit` is raised on a worker thread, where it
terminates that thread and nothing else — it never reaches the event loop it
would have had to kill. My analysis of `BaseException` was correct in
isolation, and irrelevant in context, because I had reasoned about the
exception without reasoning about the thread it was raised on.

**Still a real bug, at a lower severity.** The operator gets a bare 500 with no
diagnostic. Fixed in `97c49be` by raising `InvalidGoldenSet(ValueError)`,
converting back to `SystemExit` in `main()` for the CLI, and mapping to `422`
at the endpoint. But it was never a crash, and the tier ordering I built on
that assumption was wrong.

## Finding 2 — upgraded by the evidence

**The filing.** `write_markdown_report` ignores `refused` for answerable
cases, so a wrongly-refused answerable case prints `### PASS`. Filed as a
cosmetic reporting defect: wrong markdown, right numbers.

**What actually happened.** The markdown was the smaller half. For an
answerable case that retrieval refused, `gate_failures()` also returned `[]` —
**the gate passed too.** Only `answerable_refusal_rate` in the summary was
honest, and nothing gated on it.

**Why this one matters more than the first.** A cosmetic bug misleads a reader.
This one made the CI gate green while the system refused questions it should
have answered — the failure mode where a quality control reports success
*because* it is not looking. It is also the mechanism that hid a separate live
bug: the refusal pattern `(tell|show|give) me` hard-refused "Tell me about
Apollo", which is adversarial case `adv-004`, expected ANSWERABLE. The suite
stayed green for weeks. Fixed in `ca39f3c`; removing the pattern moved
adversarial recall@5 from 0.800 to 0.900.

---

## Three smaller corrections from the same session

- **A finding filed as a CRLF bug is not one.** `load_jsonl` tests `if line`,
  which is falsy only for the empty string, so a **whitespace-only** line
  reaches `json.loads(" ")` and raises. CRLF is handled correctly by
  `splitlines()`. Filing it as a line-ending bug would have sent the fix to the
  wrong place.
- **A perfect score was the tautology, not the achievement.** The live eval
  returned `recall@5: 1.0000` across 40 answerable cases and `GATE PASSED`. The
  golden expectations were the question's own keywords. A 1.000 that cannot go
  down is not a measurement.
- **The reviewer missed one that reading alone would also have missed.**
  `docs/deployment-strategy.md` pinned Qdrant at a version the running stack
  had already moved past. Found by comparing the document to the container, not
  by reading either one.

## One finding I could not reproduce, and did not close

The rate limiter reads the clock as `ARGV[3]` from the application rather than
calling `redis.call("TIME")` inside the Lua script, so two callers can refill
one bucket from different clocks. I confirmed the mechanism directly — the
bucket `rate-limit:query:127.0.0.1` holds `updated_at = <app clock ms>`.

I could not demonstrate the consequence. Twenty-six requests never produced a
`429`; tokens bottomed out at 3.04. The limiter works. Exhaustion is untested.

The finding stayed open with that noted, because "I proved the mechanism and
failed to trigger the symptom" is a different state from "verified" and should
not be filed as either fixed or false.

---

## What this cost and what it returned

One session with the stack running. It moved my top-priority item down,
promoted a cosmetic item into the one that had been hiding a live refusal bug,
re-pointed one fix at the right root cause, and invalidated a headline metric.

Every one of those errors was in my own analysis, produced by reading code
carefully and never running it. Reading finds candidates. Only running decides.
