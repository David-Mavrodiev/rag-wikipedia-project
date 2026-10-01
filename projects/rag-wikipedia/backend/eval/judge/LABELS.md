# How a claim is labelled

The rules were written before the claims were read, and they are the only thing
a label may depend on. They decide ONE question:

> Does the context the answer was generated from state or directly imply the
> claim?

Nothing else. Not whether the claim is true in the world, not whether it answers
the question, not whether the system should have refused.

## The rules

1. **`supported`** - every assertion the claim makes is stated by the context, or
   follows from it without new facts. The sentences may be anywhere in any
   chunk: "D. J. Bonebrake" in the first sentence and the 2021 revelation in the
   seventh is *supported*, because both are there.
2. **`unsupported`** - some assertion is not in the context. A detail carried
   over from the question counts as an assertion: if the context does not state
   it, the claim is unsupported even when the detail is true in the world.
3. **`contradicted`** - the context states the opposite. A subset of
   unsupported, recorded separately because it is the worse failure.
4. Rephrasing is free. "killed" for "was involved in the murder" is supported
   only if the context's wording carries that meaning; "involved in" does not
   state who struck the blow, so a claim that he killed him is *unsupported*.
5. Rounding, unit conversion and arithmetic the context's numbers determine are
   supported. Any other number is not.
6. A claim that is only a name, with no assertion ("Party on the Enterprise"),
   is labelled `name_only` and left out of the supported/unsupported rates: an
   NLI judge is not being asked a question it can answer. Whether the name is
   the right answer is recorded as `answers_question`.

## Two axes, never mixed

| field | question |
|---|---|
| `label` | does the context support the claim? (the judge's job) |
| `answers_question` | is this a correct answer to the question asked? (not the judge's job) |

`sd-u-010` is why both exist: the context really does say the AIM-54 Phoenix
scored 78 victories, so the claim is `supported`, and it is still a wrong answer
to a question about a different missile - `answers_question: false`. A
groundedness judge cannot catch that, and reporting it as if it could would
overstate what the number means.

## Who labelled, and how to audit it

The labels were made by Claude (Opus 5) reading each claim against its full
retrieved context, with a one-line `reason` recorded for every claim. They are
not a human gold standard. `scripts/audit_judge_labels.py --sample 20` prints a
random sample with its reasons for a person to check, and disagreements belong
in this file as they are found.

## What the labels say about the judge today

`real_claims.jsonl.gz`, the 53 claims of the first end-to-end run: 46 supported,
3 unsupported, 4 name-only. Against the judge that produced the published
0.792 (`cross-encoder/nli-deberta-v3-base`, threshold 0.5):

| | value |
|---|---:|
| agreement on the 49 propositional claims | 0.816 (40/49) |
| precision of an "unsupported" flag | 0.200 (2 of 10 flags) |
| recall of unsupported claims | 0.667 (2 of 3) |
| name-only claims scored above 0.5 | 3 of 4 (0.989, 0.987, 0.983, and 0.030) |

So the judge is not measuring groundedness so much as a lower bound on it: it
flags five claims for every real one, and it still missed `sd-a-037`, which
merges two vehicles the context keeps apart and passed at 0.943. The name-only
scores are the clearest sign that a bare noun phrase is not a question an NLI
model answers: three near 0.99 and one at 0.03, for the same kind of claim.

**These dev labels were written while the judge's verdicts were visible** - the
flagged claims were read first, during the error analysis in
`docs/measurement-baseline.md`. That is the reason dev cannot be the measure of
a judge change: see the splits below.

## Splits

| split | claims | used for |
|---|---|---|
| `dev` | the first end-to-end run (`serving_detail`, 2026-09-21/22) | designing judge changes |
| `test` | runs generated afterwards | one measurement per judge version, after the design is frozen |

A judge change that is designed on `dev` and then reported on `dev` measures
nothing. The synthetic set (`scripts/build_judge_set.py`) carries its own
dev/test split by passage, for the same reason.
