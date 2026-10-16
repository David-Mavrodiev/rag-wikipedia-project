## Judge accuracy: `real_claims` / test

_judge `cross-encoder/nli-deberta-v3-base` | assemble 3 | threshold 0.5 | 57 propositional claims, 0 name-only_

| | value |
|---|---:|
| agreement | 0.930 |
| precision of an unsupported flag | 1.000 |
| recall of unsupported claims | 0.200 |
| expected calibration error | 0.059 |

| kind | n | agreement |
|---|---:|---:|
| `supported` | 52 | 1.000 |
| `unsupported` | 5 | 0.200 |

| score | n | share actually supported |
|---|---:|---:|
| 0.0-0.2 | 1 | 0.000 |
| 0.6-0.8 | 1 | 0.000 |
| 0.8-1.0 | 55 | 0.946 |

**Passed but unsupported** (what the rate does not see): `sg-a-033#0`, `sg-a-033#3`, `sg-a-051#2`, `sg-u-001#0`
