## Engine comparison

_corpus: wikipedia_eval | model: llama3.2:3b | embedder: BAAI/bge-small-en-v1.5_

### `holdout`

| metric | `direct` | `langgraph` | delta | |
|---|---:|---:|---:|---|
| `false_accept_rate` | 0.000 | 0.000 | +0.000 | lower is better |
| `refusal_accuracy` | 1.000 | 1.000 | +0.000 | higher is better |
| `answerable_refusal_rate` | 0.067 | 0.133 | +0.067 | the price of the above |
| `mean_llm_calls` | 0.900 | 0.900 | +0.000 | cost |
| `mean_latency_ms` | 61434.875 | 59828.815 | -1606.060 | cost |

### `adversarial`

| metric | `direct` | `langgraph` | delta | |
|---|---:|---:|---:|---|
| `false_accept_rate` | 0.100 | 0.100 | +0.000 | lower is better |
| `refusal_accuracy` | 0.900 | 0.900 | +0.000 | higher is better |
| `answerable_refusal_rate` | 0.000 | 0.200 | +0.200 | the price of the above |
| `mean_llm_calls` | 0.750 | 0.750 | +0.000 | cost |
| `mean_latency_ms` | 55628.095 | 47781.295 | -7846.800 | cost |

_false_accept_rate and answerable_refusal_rate must be read together: any engine can drive the first to zero by refusing everything._
