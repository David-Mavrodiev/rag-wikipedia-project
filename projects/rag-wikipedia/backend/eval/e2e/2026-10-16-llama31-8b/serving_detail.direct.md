## End-to-end: `serving_detail` / `direct`

_corpus `wikipedia` (87173 vectors, profile `real`) | model `llama3.1:8b` | judge `cross-encoder/nli-deberta-v3-base` | git `0983f9f22add0f55c79fad601fdc3b80426525f6`_

| refusal | value | | answers | value |
|---|---:|---|---|---:|
| false_accept_rate | 0.000 | | answered | 55 |
| refusal_accuracy | 1.000 | | supported_sentence_rate | 0.881 |
| answerable_refusal_rate | 0.083 | | fully_supported_answer_rate | 0.868 |
| context_hit_rate | 0.950 | | citation_valid_rate | 1.000 |
| soft refusals (article in context) | 3 (2) | | placeholder_citation_rate | 0.000 |

| latency (ms) | n | p50 | p95 | min | max |
|---|---:|---:|---:|---:|---:|
| generated | 74 | 3528 | n/a | 2274 | 78853 |
| generated_full | 74 | 3528 | n/a | 2274 | 78853 |
| generated_throttled | 0 | n/a | n/a | n/a | n/a |
| generated_unknown | 0 | n/a | n/a | n/a | n/a |
| no_generation | 6 | 32 | n/a | 31 | 37 |

_GPU: Tesla T4; paced at None/None C; cooled None s in None pause(s). p50 needs 5 samples, p95 100. A claim is supported at entailment >= 0.5._

### Unsupported claims

- `sd-a-006` (0.01): The MySpace Movie portrays David Lehre as its "creator".
- `sd-a-014` (0.02): Dave Grohl revealed in 2021 that he and Donald J. Bonebrake are cousins through Grohl's grandmother.
- `sd-a-028` (0.41): Note: I corrected "volts per microsecond" to "volts per second (or microsecond)" as it was written in the original text.
- `sd-a-043` (0.04): Since 1 January 1988, Mittarfeqarfiit, the Greenland Airport Administration, has operated the airport formerly known as Bluie West One.
- `sd-a-049` (0.08): The unknown master-builder of the tomb of the Benedictine abbot Erminold is known in art history as the "Erminold Master".
- `sd-a-051` (0.00): The Mall at Rockingham Park is managed by Simon Property Group, which also owns 28.2% of it.
- `sd-a-059` (0.04): Bob Hancocks holds both the most goals and most appearances records for Southam United F.C..
