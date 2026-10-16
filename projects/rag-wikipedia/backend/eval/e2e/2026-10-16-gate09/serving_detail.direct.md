## End-to-end: `serving_detail` / `direct`

_corpus `wikipedia` (87173 vectors, profile `real`) | model `llama3.2:3b` | judge `cross-encoder/nli-deberta-v3-base` | git `da13cb44ec43d5defaab0d99d921d8f910d9fbb8`_

| refusal | value | | answers | value |
|---|---:|---|---|---:|
| false_accept_rate | 0.000 | | answered | 49 |
| refusal_accuracy | 1.000 | | supported_sentence_rate | 0.837 |
| answerable_refusal_rate | 0.183 | | fully_supported_answer_rate | 0.837 |
| context_hit_rate | 0.867 | | citation_valid_rate | 1.000 |
| soft refusals (article in context) | 3 (3) | | placeholder_citation_rate | 0.000 |

| latency (ms) | n | p50 | p95 | min | max |
|---|---:|---:|---:|---:|---:|
| generated | 52 | 2421 | n/a | 1570 | 74484 |
| generated_full | 52 | 2421 | n/a | 1570 | 74484 |
| generated_throttled | 0 | n/a | n/a | n/a | n/a |
| generated_unknown | 0 | n/a | n/a | n/a | n/a |
| no_generation | 28 | 823 | n/a | 701 | 982 |

_GPU: Tesla T4; paced at None/None C; cooled None s in None pause(s). p50 needs 5 samples, p95 100. A claim is supported at entailment >= 0.5._

### Unsupported claims

- `sd-a-013` (0.01): The Westminster Court of Burgesses governed the former abbey lands immediately west of London's financial district from 1585 to 1900.
- `sd-a-014` (0.02): Dave Grohl revealed in 2021 that he and Donald J. Bonebrake are cousins through Grohl's grandmother.
- `sd-a-044` (0.14): The Portuguese version of Shakira's 1996 single "Pies Descalzos, Sueños Blancos" is called "Pés Descalços".
- `sd-a-049` (0.05): The unknown master-builder is known in art history as the "Erminold Master".
- `sd-a-052` (0.01): Hanns Alexander, the great-uncle of Thomas Harding, was believed by his family to have killed Gustav Simon, the Nazi Gauleiter of Luxembourg.
- `sd-a-060` (0.06): The biggest employer in Billerbeck is the company Dr. Suwelack founded by Wolfgang Suwelack, which employs 410 staff and operates one of the most advanced nutrition shockfrosting sites in Europe.
- `sd-a-064` (0.00): The SCADE Suite model-based design tool was acquired from Telelogic in 2001.
