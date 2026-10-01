## End-to-end: `serving_golden` / `direct`

_corpus `wikipedia` (87173 vectors, profile `real`) | model `llama3.2:3b` | judge `cross-encoder/nli-deberta-v3-base` | git `a8f548a6b268e78cc4419df30c50a2490fb7ccb6`_

| refusal | value | | answers | value |
|---|---:|---|---|---:|
| false_accept_rate | 0.167 | | answered | 44 |
| refusal_accuracy | 0.833 | | supported_sentence_rate | 0.995 |
| answerable_refusal_rate | 0.350 | | fully_supported_answer_rate | 0.955 |
| context_hit_rate | 0.933 | | citation_valid_rate | 0.841 |
| soft refusals (article in context) | 19 (17) | | placeholder_citation_rate | 0.068 |

| latency (ms) | n | p50 | p95 | min | max |
|---|---:|---:|---:|---:|---:|
| generated | 73 | 6469 | n/a | 1484 | 31969 |
| generated_full | 0 | n/a | n/a | n/a | n/a |
| generated_throttled | 73 | 6469 | n/a | 1484 | 31969 |
| generated_unknown | 0 | n/a | n/a | n/a | n/a |
| no_generation | 17 | 0 | n/a | 0 | 125 |

_GPU: NVIDIA GeForce RTX 3060 Laptop GPU; paced at 90/87 C; cooled 0.0 s in 0 pause(s). p50 needs 5 samples, p95 100. A claim is supported at entailment >= 0.5._

### Unsupported claims

- `sg-a-033` (0.02): The system was mainly tripartite, with three types of schools: the primary school, the gymnasium, and the university.
- `sg-a-049` (0.01): This, in turn, stems from Latin 'signum' indicating an"identifying mark, token, indication, symbol; proof; military standard, ensign; a signal, an omen; sign in the heavens, constellation".
