# Four supplemental options, added without moving a default

> **Status: plan, 12 Oct 2026.** Four capabilities asked for by the owner: a
> LangChain chain with an agent step, Azure AI Search with ACL trimming, those
> Azure resources in Terraform, and traces plus eval runs in MLflow.
>
> They are **add-ons** by the project's own rule - the live demo comes first -
> but each is a thing enterprise job adverts ask for, so each is built the way
> this repository already builds options: a registry entry, an optional extra, a
> lazy import, and a measurement.

## What makes an option safe here

The codebase already answers this twice: `ENGINE_REGISTRY` (`direct`,
`langgraph`) and `LLM_REGISTRY`/`EMBED_REGISTRY` (`ollama-3b`, `vertex`).
Adding one is a line; choosing one is an environment variable; the SDK is a
lazy import behind an optional extra, so CI and the default deployment install
nothing new. `tests/test_layering.py` asserts that laziness, and
`tests/test_vertex.py` proves a provider's contract on a machine that has never
authenticated to the provider.

Three constraints shape everything below:

1. **Defaults never move.** `ENGINE_CHOICE=direct`, `STORE_CHOICE=qdrant`,
   MLflow off. A deployment that does not opt in sees no change at all.
2. **`audit_freshness` watches** `retrieval.py`, `vectorstore.py`, `config.py`,
   `refusal.py`, `chunking.py`, `embeddings.py` and the eval harness. Editing
   one forces a re-measured `audit_report.json` in the same commit. The designs
   below avoid those files where it is possible, and say so where it is not.
3. **An option that is not measured is labelled unmeasured.** The suites decide
   whether a framework earned its place, including the answer "it did not".

## 1. The store becomes swappable (foundation)

`retrieve()` calls exactly one method on the store: `search(vector, top_k=…)`.
Ingestion uses `ensure_collection`, `upsert_batch`, `existing_ids`, `count`.

```
app/core/stores/base.py       VectorStore, a typing.Protocol over those methods
app/core/stores/registry.py   STORE_REGISTRY {"qdrant", "azure-search"}, STORE_CHOICE
```

**The Protocol is structural, so `QdrantStore` satisfies it without being
touched** - which keeps `vectorstore.py` (watched) out of the diff. Engines and
the harness change a type hint, not a behaviour. `STORE_CHOICE` is read from
the environment, exactly as `ENGINE_CHOICE` is, and for the same stated reason:
a field on `Settings` costs a re-measurement that this value cannot affect
until it gates served behaviour.

## 2. MLflow, without instrumenting the harness

Two surfaces, and only one of them needs code near the measurements.

**Eval runs.** The harness already writes `report_<suite>.json` with
provenance, conditions and metrics, and `eval/e2e/*.json` for end-to-end runs.
So `run_eval.py` (watched) is not touched at all: `scripts/mlflow_publish.py`
reads a finished report and logs it as an MLflow run - params from provenance
(profile, collection, top_k, model, git sha), metrics from the report, the
markdown as an artifact. It works retroactively on every report already
committed, which is the fastest way to get a history worth looking at.

**Traces.** `mlflow.langchain.autolog()` covers the graph and agent engines;
`direct` gets a thin span wrapper around retrieve / generate / cite. Both are
off unless `MLFLOW_TRACKING_URI` is set, and the tracking store is a local
directory, so this stays free.

## 3. A LangChain engine with an agent step

One line in `ENGINE_REGISTRY`, `engines/langchain_engine.py`, a `langchain`
extra, a lazy import. The engine contract is unchanged and absolute: it
**orchestrates `app.core`, it never reimplements it**. The agent's tools wrap
`retrieve()` and the refusal gate rather than deciding for themselves what
evidence or a refusal is - the moment a tool decides that, two engines stop
being two strategies over one system.

What the agent adds over `langgraph`, which already grades, rewrites and
verifies, is **tool choice**: the model decides whether to search again, narrow
the query, or answer. On `llama3.2:3b` tool-calling is weak, so the step budget
is 3 and the fallback is a refusal. `compare_engines.py` already measures
engines against each other and reports LLM calls per answer, so the comparison
includes the price. **A third engine that costs four times the calls for no
quality gain is a publishable finding, not a failure.**

## 4. Azure AI Search, with ACL trimming that actually trims

`app/core/stores/azure_search.py`, selected by `STORE_CHOICE=azure-search`.

**The filter is server-side.** The ACL predicate goes into the Azure query
(`search.in(acl_groups, 'eng,legal', ',')`), never into a Python filter after
the results come back: post-filtering leaks existence through result counts and
scores, which is the whole failure mode ACL trimming exists to prevent.

**The caller's groups are bound when the store is built, per request**, in
`app/api/query.py` - which is not watched - so `retrieve()` keeps its exact
signature and `retrieval.py` stays out of the diff.

**It fails closed.** No principal means no groups means no documents, with a
test that asserts exactly that, and a test that a document whose groups do not
intersect the caller's never appears in results.

Two honest limits:

- Wikipedia has no ACLs, so the demo assigns synthetic `acl_groups` per chunk.
  That is what makes trimming demonstrable; it is not pretending to be a real
  permission model.
- **The Azure AI Search free tier (50 MB) will not hold 87,173 chunks.** This
  runs on a subset of about 5,000 chunks, or on a Basic tier at roughly $75 a
  month. The subset is the default, and the reports say which corpus they
  describe - a capability demo, not a second production index.

## 5. Terraform for the Azure resources

`infra/terraform/`: resource group, the Search service, a managed identity and
its role assignment, outputs that feed the `.env`. The tier variable defaults
to `free`, and anything billable requires `confirm_paid_tier = true` - a
deliberate speed bump in front of a $75 line item. CI runs `fmt`, `validate`
and `plan`; it never runs `apply`.

## Order, and what each phase costs

| Phase | Effort | Why here |
|---|---|---|
| 1. Store protocol + registry | ~0.5 day | nothing else can be swapped without it |
| 2. MLflow publisher | ~1 day | cheapest value, retroactive, touches nothing watched |
| 3. LangChain agent engine | ~2 days | measurable against two engines that already exist |
| 4. Azure AI Search + ACL | ~3 days | the capability most often asked for in job adverts |
| 5. Terraform | ~1 day | needs phase 4's resources to exist |

## What was built, and what the layering test changed about it

All five phases are in this branch, in their **free** form: no billable Azure
SKU, no new paid service, and nothing installed by default or in CI.

| | Built | Verified |
|---|---|---|
| store Protocol | `app/core/stores/base.py` | `QdrantStore` satisfies it unmodified; audit-freshness still green |
| store registry | `providers/stores.py` | unknown name fails loudly; `STORE_CHOICE` honoured |
| LangChain agent engine | `engines/langchain_engine.py`, `ENGINE_CHOICE=langchain` | 16 tests: budget, parsing, and that it never re-decides what a refusal is |
| Azure AI Search + ACL | `providers/azure_search.py`, `STORE_CHOICE=azure-search` | 14 tests with a faked SDK: the filter is in the QUERY, and it fails closed |
| the subset index | `scripts/build_azure_index.py` | deterministic groups, `acl_groups` filterable, 384-d cosine |
| Terraform | `infra/terraform/` | free SKU by default, `confirm_paid_tier` in front of anything billable; `fmt`/`validate` in CI |
| MLflow publisher | `scripts/mlflow_publish.py` | 13 tests on the mapping, retroactive over committed reports |

**The layering test moved two files for me.** `tests/test_layering.py` walks the
AST and forbids a cloud SDK anywhere under `app/` - lazily imported or not - and
separately forbids `app/` importing `providers/` at all. So the Azure adapter
lives in `providers/azure_search.py` beside the Vertex one, and the store
registry lives in `providers/stores.py`. `app/core/stores/` keeps the Protocol
and nothing else. That is the architecture asserting itself, which is the point
of having the test.

**Still unmeasured, and labelled as such:** the agent engine has not been run on
the suites (that is a GPU run and a `compare_engines.py` report), and no Azure
resource has been created - `terraform validate` has never run on this machine,
because Terraform is not installed here; CI is what will check it.

Phases 3-5 are additive and off by default. They wait behind the live demo,
which is the thing a recruiter actually opens.
