# <symptom in one line> — YYYY-MM-DD

**Timebox:** 45 min | **Started:** HH:MM | **Stopped:** HH:MM
**Assistance used:** none

---

## 1. Symptom

What is observably wrong. Only what can be seen: the message, the wrong value,
the thing that did not happen. No interpretation yet.

## 2. Hypothesis — written before running anything

> Locked once section 4 begins. Do not edit it afterwards.

**I think the cause is:**

**Because:**

**Confidence (1–5):**

## 3. The test that would falsify it

**If I am right, I will see:**

**If I am wrong, I will see instead:**

A hypothesis with no distinguishing observation is not yet a hypothesis — the
two lines above must differ. If they don't, keep narrowing before you run
anything.

## 4. What actually happened

Command run, output observed. Paste the real output, not a paraphrase.

**Hypothesis held / failed:**

---

## 5. Subsequent hypotheses

Repeat 2–4 for each. Number them. Keep the dead ones.

### H2

**Cause:** · **Because:** · **Predicted:** · **Observed:** · **Held/failed:**

---

## 6. Root cause

The mechanism, at the level where the fix belongs. "It threw a null" is a
symptom; "the config is read before the env file is loaded, so every default
wins" is a cause.

## 7. Why it was not caught earlier

The most valuable section, and the one most often skipped. What test, type, or
assertion should have failed and didn't? If nothing could have caught it, say
that — it is a design finding.

## 8. The fix

What changed, and what was checked before changing it (blast radius: what else
depends on this?).

## 9. Scorecard

| | |
|---|---|
| Hypotheses before the right one | |
| First hypothesis correct? | |
| Time to root cause | |
| Time in the wrong layer | |
| Would a test have caught it? | |

## 10. What I would do differently

One sentence. Something about *method*, not about this bug.
