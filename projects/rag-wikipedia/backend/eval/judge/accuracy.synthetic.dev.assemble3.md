## Judge accuracy: `synthetic` / dev

_judge `cross-encoder/nli-deberta-v3-base` | assemble 3 | threshold 0.5 | 193 propositional claims, 0 name-only_

| | value |
|---|---:|
| agreement | 0.881 |
| precision of an unsupported flag | 1.000 |
| recall of unsupported claims | 0.830 |
| expected calibration error | 0.121 |

| kind | n | agreement |
|---|---:|---:|
| `composed` | 29 | 1.000 |
| `composed_false` | 25 | 0.760 |
| `entity_swap` | 28 | 0.750 |
| `foreign` | 28 | 0.786 |
| `negated` | 29 | 0.897 |
| `number_swap` | 25 | 0.960 |
| `verbatim` | 29 | 1.000 |

| score | n | share actually supported |
|---|---:|---:|
| 0.0-0.2 | 109 | 0.000 |
| 0.2-0.4 | 3 | 0.000 |
| 0.4-0.6 | 3 | 0.000 |
| 0.6-0.8 | 1 | 0.000 |
| 0.8-1.0 | 77 | 0.753 |

**Passed but unsupported** (what the rate does not see): `syn-0016`, `syn-0017`, `syn-0036`, `syn-0037`, `syn-0038`, `syn-0040`, `syn-0054`, `syn-0059`, `syn-0093`, `syn-0120`, `syn-0128`, `syn-0169`, `syn-0190`, `syn-0192`, `syn-0194`, `syn-0200`, `syn-0201`, `syn-0243`, `syn-0245`, `syn-0291`, `syn-0292`, `syn-0303`, `syn-0306`
