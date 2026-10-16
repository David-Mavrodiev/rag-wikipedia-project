## Judge accuracy: `synthetic` / dev

_judge `cross-encoder/nli-deberta-v3-base` | assemble 0 | threshold 0.5 | 193 propositional claims, 0 name-only_

| | value |
|---|---:|
| agreement | 0.860 |
| precision of an unsupported flag | 0.943 |
| recall of unsupported claims | 0.852 |
| expected calibration error | 0.093 |

| kind | n | agreement |
|---|---:|---:|
| `composed` | 29 | 0.759 |
| `composed_false` | 25 | 0.840 |
| `entity_swap` | 28 | 0.750 |
| `foreign` | 28 | 0.786 |
| `negated` | 29 | 0.897 |
| `number_swap` | 25 | 1.000 |
| `verbatim` | 29 | 1.000 |

| score | n | share actually supported |
|---|---:|---:|
| 0.0-0.2 | 117 | 0.043 |
| 0.2-0.4 | 4 | 0.250 |
| 0.4-0.6 | 5 | 0.200 |
| 0.6-0.8 | 2 | 0.500 |
| 0.8-1.0 | 65 | 0.769 |

**Passed but unsupported** (what the rate does not see): `syn-0017`, `syn-0038`, `syn-0040`, `syn-0054`, `syn-0059`, `syn-0093`, `syn-0120`, `syn-0128`, `syn-0169`, `syn-0190`, `syn-0192`, `syn-0194`, `syn-0200`, `syn-0201`, `syn-0243`, `syn-0245`, `syn-0291`, `syn-0292`, `syn-0303`, `syn-0306`

**Flagged but supported** (what a reader would waste time on): `syn-0035`, `syn-0056`, `syn-0155`, `syn-0175`, `syn-0196`, `syn-0288`, `syn-0349`
