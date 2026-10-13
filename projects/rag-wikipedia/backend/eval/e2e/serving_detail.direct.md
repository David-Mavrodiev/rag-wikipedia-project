## End-to-end: `serving_detail` / `direct`

_corpus `wikipedia` (87173 vectors, profile `real`) | model `llama3.2:3b` | judge `cross-encoder/nli-deberta-v3-base` | answers from git `2bcc8584b425471631e60dbaeb322d93a1bead03`, scored at git `9d14a3bbe69a8f97de049f22b6bef8772180b5f0`_

| refusal | value | | answers | value |
|---|---:|---|---|---:|
| false_accept_rate | 0.150 | | answered | 55 |
| refusal_accuracy | 0.850 | | supported_sentence_rate | 0.792 |
| answerable_refusal_rate | 0.133 | | fully_supported_answer_rate | 0.792 |
| context_hit_rate | 0.950 | | citation_valid_rate | 0.818 |
| soft refusals (article in context) | 6 (5) | | placeholder_citation_rate | 0.145 |

| latency (ms) | n | p50 | p95 | min | max |
|---|---:|---:|---:|---:|---:|
| generated | 74 | 7328 | n/a | 3094 | 64359 |
| generated_full | 0 | n/a | n/a | n/a | n/a |
| generated_throttled | 74 | 7328 | n/a | 3094 | 64359 |
| generated_unknown | 0 | n/a | n/a | n/a | n/a |
| no_generation | 6 | 94 | n/a | 47 | 109 |

_GPU: NVIDIA GeForce RTX 3060 Laptop GPU; paced at 90/87 C; cooled 5659.0 s in 61 pause(s). p50 needs 5 samples, p95 100. A claim is supported at entailment >= 0.5._

### Unsupported claims

- `sd-a-014` (0.02): Dave Grohl revealed in 2021 that he and Donald J. Bonebrake are cousins through Grohl's grandmother.
- `sd-a-031` (0.03): "Party on the Enterprise".
- `sd-a-034` (0.00): Count Pavel Dmitrievich Kiselyov was the aide-de-camp of Count Miloradovich at the Battle of Borodino, and later commanded the occupying troops in Wallachia.
- `sd-a-044` (0.04): The Portuguese version of Shakira's 1996 single "Bare Feet, White Dreams" is called "Pés Descalços".
- `sd-a-049` (0.07): The unknown master-builder of the tomb of the Benedictine abbot Erminold near Regensburg is known in art history as the "Erminold Master".
- `sd-a-051` (0.00): Simon Property Group manages the largest shopping centre in New Hampshire and owns 28.2% of it.
- `sd-a-052` (0.00): Hanns Alexander, the great-uncle of Thomas Harding, was believed by his family to have killed the Nazi Gauleiter of Luxembourg, Gustav Simon.
- `sd-a-064` (0.00): The SCADE Suite model-based design tool was acquired from Telelogic in 2001.
- `sd-u-006` (0.03): The name for non-aromatic hydrocarbons derives from the Greek word (elaion), meaning "olive oil, oil" and that from (elaia), "olive tree", "olive fruit".
- `sd-u-010` (0.08): According to, the AIM-54 Phoenix achieved 78 air-to-air kills against Iraqi MiG-21s, MiG-23s, and MiG-25s during the Iran–Iraq War.
- `sd-u-016` (0.01): The French Basque port city of Labourd developed at the confluence of the Calle-Calle, Valdivia, and Cau-Cau Rivers.
