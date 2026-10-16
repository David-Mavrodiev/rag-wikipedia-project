## Judge accuracy: `real_claims` / dev

_judge `cross-encoder/nli-deberta-v3-base` | assemble 0 | threshold 0.5 | 49 propositional claims, 4 name-only_

| | value |
|---|---:|
| agreement | 0.816 |
| precision of an unsupported flag | 0.200 |
| recall of unsupported claims | 0.667 |
| expected calibration error | 0.174 |

| kind | n | agreement |
|---|---:|---:|
| `name_only` | 4 | n/a |
| `supported` | 46 | 0.826 |
| `unsupported` | 3 | 0.667 |

| score | n | share actually supported |
|---|---:|---:|
| 0.0-0.2 | 10 | 0.800 |
| 0.4-0.6 | 1 | 1.000 |
| 0.8-1.0 | 38 | 0.974 |

**Passed but unsupported** (what the rate does not see): `sd-a-037`

**Flagged but supported** (what a reader would waste time on): `sd-a-014`, `sd-a-034`, `sd-a-044`, `sd-a-049`, `sd-a-051`, `sd-a-052`, `sd-a-064`, `sd-u-010`

**Name-only claims**, counted apart: `sd-a-007` 0.99, `sd-a-021` 0.99, `sd-a-031` 0.03, `sd-a-043` 0.98
