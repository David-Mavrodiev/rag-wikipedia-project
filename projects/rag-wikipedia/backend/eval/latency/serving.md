## Serving latency

_80 distinct questions against `http://127.0.0.1:8000` | GPU `NVIDIA GeForce RTX 3060 Laptop GPU` | paced at 90/86 C, cooled 1384.2 s | git `89e9826bb30e`_

| group | n | p50 ms | p95 ms | min ms | max ms |
|---|---:|---:|---:|---:|---:|
| generated | 68 | 7,061 | n/a | 1,740 | 14,092 |
| generated_throttled | 68 | 7,061 | n/a | 1,740 | 14,092 |
| no_generation | 12 | 0 | n/a | 0 | 0 |

Stage split of warm generated requests (p50 ms):

deps 0 | embed 26 | search 51 | retrieve 88 | generate 6,957 | total 7,061

_Server-side `total` from Server-Timing. p50 needs 5 samples, p95 100; smaller groups show their range only. `cold` = the request paid lazy model loading (deps >= 1000 ms)._
