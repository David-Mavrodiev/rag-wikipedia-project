# Timed debug log

One unassisted debugging session per week, timed, with the hypothesis written
down **before** anything is run.

## Why unassisted, when AI is allowed

Because the thing being trained is not typing speed. Reading a stack trace,
recalling an API, and generating a plausible fix are all cheap now. What stayed
expensive is deciding whether the plausible fix is *correct*, knowing which
measurement would settle it, and noticing when a confident explanation — a
model's or your own — is wrong.

That skill is exercised by **committing to a hypothesis before you have
feedback**. Assistance is what removes the commitment step: it is very easy to
read a suggestion, agree with it, and never find out whether you would have got
there. So the log is unassisted, and the rest of the week is not.

The other reason is mundane: interviews are still often unassisted and timed.

## Rules

1. **Hypothesis first.** Write sections 1–3 before running anything. Once you
   start, do not edit them. A hypothesis revised after the evidence is a
   summary, not a prediction.
2. **Timebox it.** 45 minutes. Stop at the buzzer and write up wherever you got
   to. An unfinished entry is data; a missing entry is not.
3. **Record wrong predictions in full.** They are the point. An entry where the
   first hypothesis was right is the *least* informative kind.
4. **One measurement per hypothesis.** Name what you expect to see, then look.
   Changing two things and observing an improvement tells you nothing about
   which one caused it.

## Reviewing

Every ~6 entries, read them together and answer one question: **what kind of
hypothesis do I get wrong?** Layer confusions (blaming the library for a
wrapper's bug), scope errors (right mechanism, wrong blast radius), or
measurement errors (trusting a number that could not have gone down)?

That pattern is the actual output of this exercise. Individual bugs are
disposable.

## Files

- `TEMPLATE.md` — copy to `YYYY-MM-DD-short-slug.md` to start an entry.
