## Judge accuracy: `synthetic` / test

_judge `cross-encoder/nli-deberta-v3-base` | assemble 3 | threshold 0.5 | 201 propositional claims, 0 name-only_

| | value |
|---|---:|
| agreement | 0.891 |
| precision of an unsupported flag | 1.000 |
| recall of unsupported claims | 0.842 |
| expected calibration error | 0.119 |

| kind | n | agreement |
|---|---:|---:|
| `composed` | 31 | 1.000 |
| `composed_false` | 22 | 0.773 |
| `entity_swap` | 30 | 0.700 |
| `foreign` | 30 | 0.867 |
| `negated` | 30 | 0.900 |
| `number_swap` | 27 | 0.963 |
| `verbatim` | 31 | 1.000 |

| score | n | share actually supported |
|---|---:|---:|
| 0.0-0.2 | 109 | 0.000 |
| 0.2-0.4 | 8 | 0.000 |
| 0.4-0.6 | 1 | 0.000 |
| 0.6-0.8 | 4 | 0.000 |
| 0.8-1.0 | 79 | 0.785 |

**Passed but unsupported** (what the rate does not see): `syn-0012`, `syn-0043`, `syn-0045`, `syn-0067`, `syn-0078`, `syn-0080`, `syn-0085`, `syn-0104`, `syn-0106`, `syn-0132`, `syn-0134`, `syn-0137`, `syn-0185`, `syn-0257`, `syn-0265`, `syn-0266`, `syn-0269`, `syn-0270`, `syn-0276`, `syn-0278`, `syn-0356`, `syn-0392`
