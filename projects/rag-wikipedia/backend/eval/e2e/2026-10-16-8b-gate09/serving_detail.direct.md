## End-to-end: `serving_detail` / `direct`

_corpus `wikipedia` (87173 vectors, profile `real`) | model `llama3.1:8b` | judge `cross-encoder/nli-deberta-v3-base` | git `0983f9f22add0f55c79fad601fdc3b80426525f6`_

| refusal | value | | answers | value |
|---|---:|---|---|---:|
| false_accept_rate | 0.000 | | answered | 50 |
| refusal_accuracy | 1.000 | | supported_sentence_rate | 0.896 |
| answerable_refusal_rate | 0.167 | | fully_supported_answer_rate | 0.896 |
| context_hit_rate | 0.867 | | citation_valid_rate | 1.000 |
| soft refusals (article in context) | 2 (2) | | placeholder_citation_rate | 0.000 |

| latency (ms) | n | p50 | p95 | min | max |
|---|---:|---:|---:|---:|---:|
| generated | 52 | 4248 | n/a | 2350 | 75068 |
| generated_full | 52 | 4248 | n/a | 2350 | 75068 |
| generated_throttled | 0 | n/a | n/a | n/a | n/a |
| generated_unknown | 0 | n/a | n/a | n/a | n/a |
| no_generation | 28 | 703 | n/a | 662 | 891 |

_GPU: Tesla T4; paced at None/None C; cooled None s in None pause(s). p50 needs 5 samples, p95 100. A claim is supported at entailment >= 0.5._

### Unsupported claims

- `sd-a-014` (0.02): Dave Grohl revealed that he and Donald J. Bonebrake are cousins through Grohl's grandmother.
- `sd-a-034` (0.04): The Russian count who later commanded the occupying troops in Wallachia, Count Pavel Kiselyov, served as Alexander I's aide-de-camp at the close of the campaign.
- `sd-a-036` (0.04): Kekova island is a common excursion from Kaş, which is the Turkish resort known for its submerged Douglas DC-3.
- `sd-a-049` (0.08): The unknown master-builder of the tomb of the Benedictine abbot Erminold is known in art history as the "Erminold Master".
- `sd-a-051` (0.01): The Mall at Rockingham Park is managed by Simon Property Group, which owns 28.2% of it.
