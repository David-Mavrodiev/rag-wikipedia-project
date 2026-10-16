# The judge, measured - and the upgrade that did not survive the blind split

The published groundedness rate says how many claims an entailment model found
supported by the text they were generated from. That number is a statement about
the **judge** before it is a statement about the RAG, so the judge was scored
against claims labelled by hand, half of them labelled blind.

Two sets, each split by passage so no passage appears in both halves:

| set | what it is | n (test) |
|---|---|---:|
| `real_claims` | every claim of a real end-to-end run, read against its full retrieved context | 57 |
| `synthetic` | claims built from passages no suite uses, labelled by construction: verbatim and composed (supported); number and entity swaps, negations, foreign sentences, and `composed_false` (unsupported) | 201 |

**The dev half cannot score a change.** Its labels were written while the
judge's verdicts were visible, during the baseline's error analysis. That is
recorded in [LABELS.md](../backend/eval/judge/LABELS.md) and it is the reason the
test half was labelled blind and left alone until there was something to decide.

## What the judge does

| | real, dev | real, test | synthetic, dev | synthetic, test |
|---|---:|---:|---:|---:|
| claims | 49 | 57 | 193 | 201 |
| agreement | 0.816 | 0.930 | 0.860 | 0.846 |
| precision of a flag | 0.200 | 1.000 | 0.943 | 0.915 |
| recall of unsupported | 0.667 | 0.200 | 0.852 | 0.856 |

**Its errors are not one-directional, and that matters more than the agreement.**
On the real dev half it cried wolf: four of five flags were claims that were in
fact supported. On the real test half it did the reverse - every flag it raised
was right, and it passed four of the five unsupported claims (`sg-a-033#0`,
`sg-a-033#3`, `sg-a-051#2`, `sg-u-001#0`). So the published rate is **not a
floor**: on blind labels the judge misses more than it invents, which makes the
rate flattering rather than conservative. The earlier claim that 0.792 was a
floor rested on the dev half alone and is withdrawn.

**Both real halves are small.** Five unsupported claims means recall moves 0.2
per claim. The defensible sentence is "the judge misses unsupported claims, and
we measured that", not a recall figure quoted to three decimals.

**Where it fails is legible.** On the synthetic test half, by construction:

| kind | n | agreement |
|---|---:|---:|
| verbatim | 31 | 1.000 |
| number_swap | 27 | 0.963 |
| negated | 30 | 0.900 |
| foreign | 30 | 0.867 |
| composed_false | 22 | 0.818 |
| entity_swap | 30 | 0.733 |
| composed | 31 | 0.645 |

A claim copied from the source is never missed. A claim whose fact is spread
across sentences is wrong a third of the time, and a swapped entity a quarter of
the time. Those two are the judge's real limits, and the first is what the
candidate upgrade was built for.

## The upgrade: `assemble=k`

`sentence_support(assemble=k)` builds one premise from the k sentences of a chunk
that best overlap the claim, so a fact spread over two sentences can be matched.
It is off by default. On the dev half it looked like a clear win - agreement
0.860 to 0.881, flag precision 0.943 to 1.000 - which is exactly why it was not
shipped on that evidence.

The per-kind breakdown of the same dev run says what the aggregate hid:

| kind | assemble 0 | assemble 3 |
|---|---:|---:|
| composed | 0.759 | **1.000** |
| composed_false | 0.840 | **0.760** |
| number_swap | 1.000 | 0.960 |
| entity_swap, foreign, negated, verbatim | unchanged | unchanged |

It does what it was designed to do, and pays for it where the set was designed to
catch it: `composed_false` exists only to expose a judge that manufactures
support by assembling several sentences, and it recorded the manufacture. Recall
fell with it, 0.852 to 0.830.

On the blind real half it changed nothing that matters: 14 of 57 claim scores
moved, every one of them upward inside the confident band (0.988 to 0.9945),
and **not one verdict flipped** - identical agreement, precision, recall and the
same four missed claims.

On the blind synthetic half it reproduces the dev trade, in both directions:

| kind | assemble 0 | assemble 3 |
|---|---:|---:|
| composed | 0.645 | **1.000** |
| composed_false | 0.818 | 0.773 |
| entity_swap | 0.733 | 0.700 |
| number_swap, foreign, negated, verbatim | unchanged | unchanged |
| **agreement** | 0.846 | **0.891** |
| **precision of a flag** | 0.915 | **1.000** |
| **recall of unsupported** | 0.856 | 0.842 |

## The decision: not as the default, and here is why

The aggregate improves on labels it was never fitted on - agreement 0.846 to
0.891, flag precision 0.915 to 1.000 - and it completely fixes the category it
was built for. It is still not enabled for the published rate.

**Because precision rises by flagging less.** The same assembly that lets a
claim spread over two sentences be matched also lets a FALSE composite be
matched, which is the only thing `composed_false` exists to detect: 0.818 to
0.773, with recall following it down, 0.856 to 0.842. For a number that is
published as evidence the system does not fabricate, recall is the property that
matters - a missed unsupported claim is an overstatement of groundedness, and
the blind real half already showed the judge missing four of five. Trading
recall for precision makes the published figure kinder, not truer.

**And it would not have changed the figure anyway.** On the real claims the
published rate is computed from, assemble=3 moved 14 of 57 scores and flipped no
verdict.

So: `assemble` stays off by default; `assemble=3` is the better setting for a
judge used as a FILTER, where a false alarm costs a reader's time rather than a
published claim's honesty. This project does not have that use: the rule in
[hallucination-controls.md](hallucination-controls.md) is that the serving guard
may never be the model that grades us.

What this run also settles: the judge's two worst categories are composed facts
(0.645) and entity swaps (0.733), the second untouched by anything tried here.
An entity swap is a claim that says the right kind of thing about the wrong
thing - the same failure mode as `sd-u-010`, which quotes its context faithfully
about the wrong missile. That is the next thing worth attacking, and it is not
an assembly problem.

## Reproduce

```bash
SHA=$(git rev-parse HEAD)
modal run infra/modal/demo.py::judge --sets real,synthetic --split test \
    --assemble 0,3 --git-sha $SHA
```

Eight CPUs, about 45 minutes per set, a few cents. The judge is a CPU model here
and the laptop had no memory to spare; the reports are committed next to the
labelled sets in `backend/eval/judge/`.
