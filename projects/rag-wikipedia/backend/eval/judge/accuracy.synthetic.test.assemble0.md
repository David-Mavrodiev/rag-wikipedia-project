## Judge accuracy: `synthetic` / test

_judge `cross-encoder/nli-deberta-v3-base` | assemble 0 | threshold 0.5 | 201 propositional claims, 0 name-only_

| | value |
|---|---:|
| agreement | 0.846 |
| precision of an unsupported flag | 0.915 |
| recall of unsupported claims | 0.856 |
| expected calibration error | 0.134 |

| kind | n | agreement |
|---|---:|---:|
| `composed` | 31 | 0.645 |
| `composed_false` | 22 | 0.818 |
| `entity_swap` | 30 | 0.733 |
| `foreign` | 30 | 0.867 |
| `negated` | 30 | 0.900 |
| `number_swap` | 27 | 0.963 |
| `verbatim` | 31 | 1.000 |

| score | n | share actually supported |
|---|---:|---:|
| 0.0-0.2 | 122 | 0.090 |
| 0.2-0.4 | 8 | 0.000 |
| 0.4-0.6 | 1 | 0.000 |
| 0.6-0.8 | 5 | 0.200 |
| 0.8-1.0 | 65 | 0.769 |

**Passed but unsupported** (what the rate does not see): `syn-0012`, `syn-0043`, `syn-0045`, `syn-0067`, `syn-0078`, `syn-0080`, `syn-0085`, `syn-0104`, `syn-0132`, `syn-0134`, `syn-0137`, `syn-0185`, `syn-0257`, `syn-0265`, `syn-0266`, `syn-0269`, `syn-0270`, `syn-0276`, `syn-0278`, `syn-0392`

**Flagged but supported** (what a reader would waste time on): `syn-0076`, `syn-0110`, `syn-0141`, `syn-0161`, `syn-0182`, `syn-0222`, `syn-0229`, `syn-0249`, `syn-0308`, `syn-0315`, `syn-0389`
