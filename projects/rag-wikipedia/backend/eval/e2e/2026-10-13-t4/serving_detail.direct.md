## End-to-end: `serving_detail` / `direct`

_corpus `wikipedia` (87173 vectors, profile `real`) | model `llama3.2:3b` | judge `cross-encoder/nli-deberta-v3-base` | git `75b1c5d1c2c5e64f4ec64cbcdb296eb531dd07f6`_

| refusal | value | | answers | value |
|---|---:|---|---|---:|
| false_accept_rate | 0.150 | | answered | 54 |
| refusal_accuracy | 0.850 | | supported_sentence_rate | 0.812 |
| answerable_refusal_rate | 0.150 | | fully_supported_answer_rate | 0.812 |
| context_hit_rate | 0.950 | | citation_valid_rate | 1.000 |
| soft refusals (article in context) | 7 (6) | | placeholder_citation_rate | 0.000 |

| latency (ms) | n | p50 | p95 | min | max |
|---|---:|---:|---:|---:|---:|
| generated | 74 | 1793 | n/a | 1183 | 72212 |
| generated_full | 74 | 1793 | n/a | 1183 | 72212 |
| generated_throttled | 0 | n/a | n/a | n/a | n/a |
| generated_unknown | 0 | n/a | n/a | n/a | n/a |
| no_generation | 6 | 27 | n/a | 25 | 28 |

_GPU: Tesla T4; paced at None/None C; cooled None s in None pause(s). p50 needs 5 samples, p95 100. A claim is supported at entailment >= 0.5._

### Unsupported claims

- `sd-a-013` (0.01): The Westminster Court of Burgesses governed the former abbey lands immediately west of London's financial district from 1585 to 1900.
- `sd-a-014` (0.02): Dave Grohl revealed in 2021 that he and Donald J. Bonebrake are cousins through Grohl's grandmother.
- `sd-a-044` (0.14): The Portuguese version of Shakira's 1996 single "Pies Descalzos, Sueños Blancos" is called "Pés Descalços".
- `sd-a-049` (0.05): The unknown master-builder is known in art history as the "Erminold Master".
- `sd-a-052` (0.00): Hanns Alexander, the great-uncle of Thomas Harding, was believed by his family to have killed the Nazi Gauleiter of Luxembourg.
- `sd-a-064` (0.00): The SCADE Suite model-based design tool was acquired from Telelogic in 2001.
- `sd-u-006` (0.03): The name for non-aromatic hydrocarbons derives from the Greek word (elaion), meaning "olive oil, oil", and that from (elaia), "olive tree", "olive fruit".
- `sd-u-010` (0.11): The AIM-54 Phoenix had 78 air-to-air kills against Iraqi MiG-21s, MiG-23s, and MiG-25s during the Iran–Iraq War.
- `sd-u-016` (0.00): The French Basque port city of Labourd developed at the confluence of the Nive and Ossau rivers.
