# Deployment Strategy — RAG over Wikipedia on Azure (Container Apps via Azure CLI)

## 1. Overview & goals

This document defines how the RAG-over-Wikipedia system is deployed to Azure and
why. The system is four containers that today run locally under Docker Compose:

| Compose service | Role | Port | Stateful |
|---|---|---|---|
| `frontend` | React build served by nginx; reverse-proxies API calls | 80 | no |
| `api` | FastAPI backend (`/query`, `/health`) | 8000 | no |
| `qdrant` | Vector database | 6333 | yes (`/qdrant/storage`) |
| `ollama` | Local LLM runtime (`llama3.2:3b`) | 11434 | yes (`/root/.ollama`) |

Goals of the deployment:

- Run all four services on Azure with the **Azure CLI (`az`)** as the deployment mechanism.
- Keep the two stateful services (Qdrant, Ollama) private and persistent.
- Expose a single public HTTPS surface to users.
- Pull images without static registry credentials.
- Preserve the **pre-existing resource group** — it is never created or deleted by this process.

Principle: **az-CLI-first**. Every resource is created with `az`. Infrastructure-as-code
(Bicep / `azd`) is noted as an alternative in §13 but is not the primary path.

> Scope note: this is a strategy document. The command blocks are *representative* —
> they show the shape and order of the deployment, not an exhaustive, parameter-complete
> script. Treat the [Corrections](#16-corrections-vs-deployment_promptmd) section as
> authoritative where it differs from the repo's older `DEPLOYMENT_PROMPT.md`.

## 2. Platform decision

**Recommendation: Azure Container Apps (ACA).**

ACA is a serverless container platform that runs multiple independently-scaled
containers in a shared environment, with built-in HTTPS ingress, internal-only
ingress for private services, managed-identity registry pulls, Azure Files volume
mounts, and per-app autoscaling (including scale-to-zero). That maps almost
one-to-one onto this Compose topology with no cluster to operate.

| Option | Fit for this app | Ops burden | Internal networking | Scaling | GPU path | Verdict |
|---|---|---|---|---|---|---|
| **Azure Container Apps** | 4 microservices, mixed public/private, volumes | Low (serverless) | Native internal ingress | Per-app autoscale + pinned replicas | Dedicated GPU profiles | **Chosen** |
| App Service (Web App for Containers) | Strong for a single web container; multi-container/sidecar story is weak and the internal-only private services need extra VNet wiring | Low–medium | Requires VNet integration | Per-plan | Limited | Rejected — awkward for the private stateful pair |
| AKS (Kubernetes) | Handles anything | High (cluster lifecycle, upgrades, manifests) | Full (Services/NetworkPolicy) | HPA/manual | Node pools | Overkill for 4 services |
| Single VM + Docker Compose | Lift-and-shift of the existing `docker-compose.yml` | Medium–high (patching, TLS, restarts) | localhost | Manual only | Attach GPU VM | Rejected — least cloud-native, single point of failure |

Why ACA wins here: the app is already containerized, needs exactly one public
entry point, needs two always-on private stateful services, and has a heavy
single component (Ollama) that benefits from a **dedicated compute profile**
without standing up a Kubernetes cluster.

## 3. Target architecture (Azure)

```
                       Internet (HTTPS)
                              │
                              ▼
                     rag-frontend (ACA)            external ingress :80
                     nginx serves SPA +
                     proxies /query,/health
                              │  (server-side, inside the ACA environment)
                              ▼
                        rag-api (ACA)              internal ingress :8000
                              │
                ┌─────────────┴──────────────┐
                ▼                             ▼
           qdrant (ACA)                  ollama (ACA)        internal ingress only
        Azure Files: qdrant-data      Azure Files: ollama-models
        Consumption profile           Dedicated D4 profile (~16 GiB)

   Images  ← Azure Container Registry      (pull via user-assigned identity, AcrPull)
   Logs    → Log Analytics workspace
   State   → Storage Account (two Azure Files shares)
```

Per-service mapping to Container Apps:

| App | Source image | Ingress | Target port | Workload profile | Persistence |
|---|---|---|---|---|---|
| `rag-frontend` | built in ACR from `frontend/` | **external** (HTTPS) | 80 | Consumption | — |
| `rag-api` | built in ACR from `backend/` | **internal** | 8000 | Consumption | — |
| `qdrant` | `qdrant/qdrant:v1.9.2` | **internal** | 6333 | Consumption | Azure Files `qdrant-data` → `/qdrant/storage` |
| `ollama` | `ollama/ollama:latest` | **internal** | 11434 | **Dedicated D4** | Azure Files `ollama-models` → `/root/.ollama` |

Design choice — **single public surface**: only `rag-frontend` is exposed to the
internet. `rag-api` uses **internal** ingress and is reached server-side by the
frontend's nginx. This is stricter than the older prompt (which exposed the API
publicly) and removes the need for any CORS configuration, because the browser
only ever talks to the frontend origin. An external-API variant is described in §5.

## 4. Azure resources

| Resource | Suggested name (var) | Purpose |
|---|---|---|
| Container Registry | `$ACR` | Store `rag-api` + `rag-frontend` images; **admin disabled** |
| User-assigned managed identity | `rag-acr-pull` (`$UAMI`) | `AcrPull` for credential-free image pulls |
| Log Analytics workspace | `rag-logs` (`$LAW`) | ACA log sink |
| Container Apps environment | `rag-env` (`$ENV`) | Hosts all four apps; workload profiles enabled |
| Workload profile | `ollama-d4` (type **D4**) | Dedicated compute for Ollama (LLM needs ~8 GB RAM) |
| Storage account + 2 file shares | `$STG` (`qdrant-data`, `ollama-models`) | Persist vectors + model weights |
| 4 Container Apps | see §3 | The services |

Suggested variables (PowerShell):

```powershell
$RG    = "<existing-resource-group>"     # already exists — do NOT create/delete
$LOC   = "eastus"                          # pick a region with ACA Dedicated D-series quota
$ACR   = "ragwiki$(Get-Random -Maximum 99999)"     # globally unique, lowercase alnum
$STG   = "ragwikistg$(Get-Random -Maximum 99999)"  # globally unique, lowercase alnum
$ENV   = "rag-env"
$LAW   = "rag-logs"
$UAMI  = "rag-acr-pull"
```

## 5. Networking & ingress

**Ingress matrix**

- `rag-frontend`: external ingress, port 80, HTTPS terminated by ACA-managed certs.
- `rag-api`: internal ingress, port 8000.
- `qdrant`, `ollama`: internal ingress only — never publicly reachable.

**The frontend → API wiring (critical).** The frontend talks to the API through
an **nginx reverse proxy**, not via browser CORS and not via a build-time API URL.
The SPA calls relative paths (`fetch('/query')`), and `frontend/nginx.conf` proxies
them:

```nginx
location /query  { proxy_pass http://api:8000/query; }
location /health { proxy_pass http://api:8000/health; }
```

The upstream `http://api:8000` is a Docker Compose service name and **does not
resolve in ACA**. This is the one deployment-enabling code change the strategy
depends on (see §16): the nginx upstream must become configurable so it can point
at the API's ACA FQDN.

Recommended approach — **configurable upstream rendered at container start**:

1. Templatize `nginx.conf` with a placeholder, e.g. `proxy_pass ${API_UPSTREAM}/query;`.
2. Add a small entrypoint that runs `envsubst` to render the template into the
   final config before nginx starts (the value is baked at startup, so nginx needs
   no runtime DNS resolver).
3. Set `API_UPSTREAM` on the `rag-frontend` app to the API's **internal** FQDN over
   HTTPS, e.g. `https://rag-api.internal.<env-default-domain>`. Add
   `proxy_ssl_server_name on;` so SNI is sent to ACA ingress.

Backend environment variables (names must match `backend/app/core/config.py`):

| Env var | Value on Azure | Notes |
|---|---|---|
| `QDRANT_URL` | `https://<qdrant-internal-fqdn>` | internal HTTPS ingress |
| `OLLAMA_URL` | `https://<ollama-internal-fqdn>` | **`OLLAMA_URL`**, not `OLLAMA_BASE_URL` |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | **`EMBED_MODEL`**, not `EMBEDDING_MODEL` |
| `LLM_MODEL` | `llama3.2:3b` | swappable |
| `COLLECTION` | `wikipedia` | Qdrant collection name |
| `PROFILE` | `tiny` or `real` | corpus size |

There is **no CORS middleware** in the API, so no `ALLOWED_ORIGINS` variable is
read or needed in the recommended (internal-API) topology.

**Alternative — external API.** If direct public access to the API is desired
(e.g., for external clients or easier `curl` verification), give `rag-api` external
ingress and set `API_UPSTREAM` to its public FQDN. This enlarges the attack surface
and would require adding a CORS layer *only if* a browser were to call the API
directly on a different origin; through the nginx proxy, same-origin still holds.
The internal-API topology is preferred.

## 6. Persistence

Both stateful services mount Azure Files shares through the ACA environment:

- `qdrant-data` → `/qdrant/storage` (vector data + collections).
- `ollama-models` → `/root/.ollama` (downloaded model weights).

The Ollama model is pulled **once** into the persistent share and survives restarts
and revisions:

```powershell
# after the ollama app is running, exec in and pull the model once
az containerapp exec -g $RG -n ollama --command /bin/sh
#   inside:  ollama pull llama3.2:3b      # then `ollama list` should show it
```

Qdrant data likewise persists across `az containerapp revision restart`. Azure Files
uses SMB; for the tiny/real corpus this latency is acceptable. If Qdrant I/O becomes
a bottleneck at the `real` profile, switch the `qdrant-data` share to **Azure Files
Premium (NFS)**.

## 7. Deployment flow (az CLI, high level)

Ordered phases. Each ends with a quick **Verify**. Commands are representative.

**Step 0 — Providers, extension, quota**

```powershell
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.Storage --wait
```

Verify: `az containerapp env workload-profile list-supported -l $LOC -o table` lists a **D4** profile; if not, choose another region.

**Step 1 — Registry + build images in ACR** (no local Docker required)

```powershell
az acr create -g $RG -n $ACR --sku Basic --admin-enabled false
az acr build -r $ACR -t rag-api:latest ./backend
# frontend is built after the API FQDN is known (Step 7)
```

Verify: `az acr repository show -n $ACR --image rag-api:latest` succeeds.

**Step 2 — Managed identity + AcrPull**

```powershell
az identity create -g $RG -n $UAMI
$UAMI_ID  = az identity show -g $RG -n $UAMI --query id -o tsv
$UAMI_PID = az identity show -g $RG -n $UAMI --query principalId -o tsv
$ACR_ID   = az acr show -g $RG -n $ACR --query id -o tsv
az role assignment create --assignee-object-id $UAMI_PID --assignee-principal-type ServicePrincipal `
  --role AcrPull --scope $ACR_ID
```

Verify: `az role assignment list --assignee $UAMI_PID --scope $ACR_ID -o table` shows `AcrPull`.

**Step 3 — Log Analytics + ACA environment + D4 profile**

```powershell
az monitor log-analytics workspace create -g $RG -n $LAW -l $LOC
$LAW_ID  = az monitor log-analytics workspace show -g $RG -n $LAW --query customerId -o tsv
$LAW_KEY = az monitor log-analytics workspace get-shared-keys -g $RG -n $LAW --query primarySharedKey -o tsv

az containerapp env create -g $RG -n $ENV -l $LOC --enable-workload-profiles `
  --logs-workspace-id $LAW_ID --logs-workspace-key $LAW_KEY
az containerapp env workload-profile add -g $RG -n $ENV `
  --workload-profile-name ollama-d4 --workload-profile-type D4 --min-nodes 1 --max-nodes 1
$ENV_ID = az containerapp env show -g $RG -n $ENV --query id -o tsv
```

Verify: `az containerapp env show -g $RG -n $ENV --query properties.provisioningState -o tsv` returns `Succeeded`.

**Step 4 — Storage account + file shares + env-storage links**

```powershell
az storage account create -g $RG -n $STG -l $LOC --sku Standard_LRS --kind StorageV2
az storage share-rm create -g $RG --storage-account $STG -n qdrant-data   --quota 50
az storage share-rm create -g $RG --storage-account $STG -n ollama-models --quota 50
$STG_KEY = az storage account keys list -g $RG -n $STG --query "[0].value" -o tsv

az containerapp env storage set -g $RG -n $ENV --storage-name qdrant-data `
  --azure-file-account-name $STG --azure-file-account-key $STG_KEY `
  --azure-file-share-name qdrant-data --access-mode ReadWrite
az containerapp env storage set -g $RG -n $ENV --storage-name ollama-models `
  --azure-file-account-name $STG --azure-file-account-key $STG_KEY `
  --azure-file-share-name ollama-models --access-mode ReadWrite
```

Verify: `az containerapp env storage list -g $RG -n $ENV -o table` lists both.

**Step 5 — Ollama (internal, Dedicated D4, persistent).** Volume mounts are
declared via YAML (representative):

```yaml
# infra/aca/ollama.yaml (excerpt)
properties:
  managedEnvironmentId: <ENV_ID>
  workloadProfileName: ollama-d4
  configuration:
    ingress: { external: false, targetPort: 11434, transport: http }
  template:
    containers:
      - image: ollama/ollama:latest
        name: ollama
        resources: { cpu: 3.5, memory: 8Gi }
        volumeMounts: [ { volumeName: models, mountPath: /root/.ollama } ]
    scale: { minReplicas: 1, maxReplicas: 1 }
    volumes: [ { name: models, storageType: AzureFile, storageName: ollama-models } ]
```

```powershell
az containerapp create -g $RG -n ollama --yaml infra/aca/ollama.yaml
# then pull the model once (see §6)
```

Verify: after the one-time pull, `ollama list` (via `az containerapp exec`) shows `llama3.2:3b`.

**Step 6 — Qdrant (internal, persistent)** — same YAML pattern, Consumption profile,
port 6333, mount `qdrant-data` → `/qdrant/storage`, pinned to 1 replica.

Verify: `az containerapp logs show -g $RG -n qdrant --tail 20` shows a clean start with no volume errors.

**Step 7 — API (internal), then build + deploy frontend**

```powershell
# 7a. API — internal ingress, pulls via managed identity, points at internal services
$QDRANT_FQDN = az containerapp show -g $RG -n qdrant --query properties.configuration.ingress.fqdn -o tsv
$OLLAMA_FQDN = az containerapp show -g $RG -n ollama --query properties.configuration.ingress.fqdn -o tsv

az containerapp create -g $RG -n rag-api --environment $ENV `
  --image "$ACR.azurecr.io/rag-api:latest" `
  --user-assigned $UAMI_ID --registry-server "$ACR.azurecr.io" --registry-identity $UAMI_ID `
  --workload-profile-name Consumption --cpu 1.0 --memory 2Gi --min-replicas 1 --max-replicas 3 `
  --ingress internal --target-port 8000 --transport http `
  --env-vars QDRANT_URL="https://$QDRANT_FQDN" OLLAMA_URL="https://$OLLAMA_FQDN" `
             EMBED_MODEL="BAAI/bge-small-en-v1.5" LLM_MODEL="llama3.2:3b" COLLECTION="wikipedia" PROFILE="tiny"
$API_FQDN = az containerapp show -g $RG -n rag-api --query properties.configuration.ingress.fqdn -o tsv

# 7b. Build the frontend (no API URL build-arg needed) and deploy with a configurable upstream
az acr build -r $ACR -t rag-frontend:latest ./frontend
az containerapp create -g $RG -n rag-frontend --environment $ENV `
  --image "$ACR.azurecr.io/rag-frontend:latest" `
  --user-assigned $UAMI_ID --registry-server "$ACR.azurecr.io" --registry-identity $UAMI_ID `
  --workload-profile-name Consumption --cpu 0.5 --memory 1Gi --min-replicas 1 --max-replicas 2 `
  --ingress external --target-port 80 --transport http `
  --env-vars API_UPSTREAM="https://$API_FQDN"
$FRONTEND_FQDN = az containerapp show -g $RG -n rag-frontend --query properties.configuration.ingress.fqdn -o tsv
```

Verify: open `https://$FRONTEND_FQDN/health` — it is proxied to the API and returns healthy.

**Step 8 — Verify end to end** — open `https://$FRONTEND_FQDN`, ask a question, and
confirm a grounded answer with citations (and a refusal when no relevant context exists).

## 8. Data ingestion

The vector store starts empty; the Prefect ingestion pipeline (`backend/pipeline/`)
must run **against the deployed Qdrant** before queries return results. Run it as a
one-off — locally pointed at the deployed share, or as a short-lived ACA Job —
with the deployed endpoint and the desired corpus profile:

```powershell
# representative: run the existing ingestion with Azure targets
$env:QDRANT_URL = "https://$QDRANT_FQDN"
$env:PROFILE     = "tiny"   # or "real" for the ~25k-article pass
# invoke the project's ingest entrypoint (see Makefile `ingest` target)
```

Re-running ingestion is idempotent/resumable (same vector count), so it is safe to
repeat. Use `tiny` to validate the pipeline, then `real` for the full corpus.

## 9. Scaling

- **`rag-api` / `rag-frontend`**: stateless → ACA Consumption autoscale. Set
  `--min-replicas 1` (avoid cold starts) and a small `--max-replicas` with an HTTP
  concurrency scale rule. They scale independently with load.
- **`ollama`**: pinned to **1 replica** on the **Dedicated D4** profile (4 vCPU /
  ~16 GiB). `llama3.2:3b` needs ~8 GB RAM; D4 gives headroom. Pinning avoids model
  reload churn and split state across replicas.
- **`qdrant`**: pinned to **1 replica** (single-writer against the Azure Files
  volume) to avoid concurrent-writer corruption.
- **GPU (optional)**: for faster inference, add a GPU workload profile (e.g. an
  NC-series profile where quota exists) and move `ollama` onto it. CPU D4 is the
  default and is sufficient for `llama3.2:3b`.

## 10. Security

- **Registry**: ACR **admin disabled**; images pulled via a **user-assigned managed
  identity** with `AcrPull` scoped to the registry only. No registry passwords.
- **Network surface**: only `rag-frontend` is public. `rag-api`, `qdrant`, and
  `ollama` use **internal** ingress and are unreachable from the internet.
- **Transport**: all ingress is HTTPS via ACA-managed certificates.
- **Secrets**: no secrets baked into images. The storage account key is used only to
  wire env-storage. **Hardening**: store the key in **Azure Key Vault** and reference
  it via the managed identity, or use identity-based file-share access.
- **CORS**: not required in the recommended topology (single origin via the nginx
  proxy); do not add a permissive `ALLOWED_ORIGINS="*"`.
- **Least privilege**: keep the AcrPull assignment scoped to `$ACR_ID`, not the RG.

## 11. Observability

- All app logs flow to the **Log Analytics** workspace (`$LAW`).
- Tail logs: `az containerapp logs show -g $RG -n <app> --tail 50 [--follow]`.
- Health: the API exposes `/health`; the frontend proxies `/health`, so
  `https://$FRONTEND_FQDN/health` is a public liveness probe for the chain.
- Revisions: `az containerapp revision list -g $RG -n <app> -o table` for rollout
  history; query container console/system logs in Log Analytics for deeper analysis.

## 11a. Per-component validation (summary)

| Component | Check |
|---|---|
| ACR | `az acr repository show` lists `rag-api` + `rag-frontend` |
| Managed identity | `AcrPull` present; apps pull without admin creds |
| ACA env | `provisioningState == Succeeded`; D4 profile present |
| Qdrant | logs clean; collection persists across a `revision restart` |
| Ollama | `ollama list` shows `llama3.2:3b` after restart (volume persisted) |
| API | internal `/health` 200; `/query` returns grounded answer + citations |
| Frontend | `https://$FRONTEND_FQDN` answers a question with sources |
| End-to-end | refusal returned when no relevant context exists |

## 12. Cost

Qualitative drivers (validate current numbers with the Azure Pricing Calculator):

- **Dedicated D4 for Ollama is the dominant cost** — it is pinned to 1 node and runs
  24/7 (no scale-to-zero), so it bills continuously. This is the main lever.
- **Consumption apps** (`rag-api`, `rag-frontend`) bill per vCPU-second + memory +
  requests and scale down between traffic; keep `min-replicas` low to save cost,
  raise to avoid cold starts.
- **Qdrant** runs 1 small Consumption replica continuously (pinned).
- **Storage**: two Azure Files shares (50 GiB quota each) — modest.
- **Log Analytics**: billed per GB ingested + retention; cap retention to control cost.

Cost-control levers: shrink/oversize the Ollama profile to actual RAM need, stop the
environment when idle for demos, trim Log Analytics retention, and prefer `PROFILE=tiny`
for non-production environments.

## 13. Environments & parameterization

A single environment is sufficient for the current scope. To support more than one
environment (e.g. `dev` / `prod`), parameterize the names and region — prefix app,
ACR, storage, env, and workspace names with the environment, and select region by
profile. The same `az` flow then provisions each environment from the same variables.

For repeatability across environments, the identical topology can be expressed as
**Bicep** (deployed with `az deployment group create`, validated first with `what-if`)
or via **`azd up`**. This document keeps the `az` CLI as the primary path per the
project's requirement; IaC is the recommended next step if the deployment is run often.

## 14. Rollout & CI/CD (optional)

- **Safe rollout**: ACA revisions allow blue/green and **traffic splitting**. Deploy a
  new revision, send a small traffic percentage to it
  (`az containerapp ingress traffic set ...`), validate, then shift to 100%.
- **CI/CD (optional)**: a GitHub Actions workflow can run `az acr build` and
  `az containerapp update` on each change, authenticating with a federated/managed
  identity. This is an automation of the same `az` flow and is not required for the
  manual strategy above.

## 15. Teardown (preserve the resource group)

Remove the workload but keep the pre-existing resource group:

```powershell
az containerapp delete -g $RG -n rag-frontend --yes
az containerapp delete -g $RG -n rag-api --yes
az containerapp delete -g $RG -n qdrant --yes
az containerapp delete -g $RG -n ollama --yes
az containerapp env delete -g $RG -n $ENV --yes
az acr delete -g $RG -n $ACR --yes
az storage account delete -g $RG -n $STG --yes
az monitor log-analytics workspace delete -g $RG -n $LAW --yes
az identity delete -g $RG -n $UAMI
# The resource group ($RG) is intentionally left in place.
```

## 16. Corrections vs `DEPLOYMENT_PROMPT.md`

The repo's older `DEPLOYMENT_PROMPT.md` is a useful runbook but drifts from the
current code. This strategy corrects the following, verified against the source:

| Older prompt says | Reality in code | Correct approach |
|---|---|---|
| API env `OLLAMA_BASE_URL` | `config.py` reads **`OLLAMA_URL`** | set `OLLAMA_URL` |
| API env `EMBEDDING_MODEL` | `config.py` reads **`EMBED_MODEL`** | set `EMBED_MODEL` |
| Lock CORS via `ALLOWED_ORIGINS` | `app/main.py` has **no CORS middleware** | none needed (single-origin via nginx proxy) |
| Build frontend with `--build-arg VITE_API_BASE_URL` | `frontend/Dockerfile` has **no such arg**; `App.tsx` calls relative `/query` | no build-arg; configure the **nginx upstream** instead |
| Frontend reaches API via baked URL | `nginx.conf` **reverse-proxies** `/query`,`/health` to `http://api:8000` | make the upstream configurable (`API_UPSTREAM`) → API FQDN |
| API on external ingress | works, but unnecessarily public | prefer **internal** API ingress; expose only the frontend |

**Deployment-enabling code change required (not implemented by this doc):** make the
frontend `nginx.conf` upstream configurable (template + `envsubst` entrypoint driven
by `API_UPSTREAM`) so it can target the API's ACA FQDN. Also pin the `qdrant` image
to a fixed tag (`qdrant/qdrant:v1.9.2`, matching `docker-compose.yml`) rather than
`latest` for reproducible deployments.

## 17. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Region lacks ACA Dedicated **D4** quota | Check `workload-profile list-supported`; pick another region or request quota |
| Azure Files **SMB latency** for Qdrant at `real` profile | Switch `qdrant-data` to Azure Files **Premium (NFS)** |
| **Storage key** exposure in env-storage config | Move key to **Key Vault**; use identity-based share access |
| Frontend can't reach API (unconfigured upstream) | Apply the configurable-upstream change in §16 before deploying the frontend |
| Doc drifts from code over time | Every claim here is grounded in current files; revisit on app changes |
| Ollama cold model state after redeploy | Model persists on `ollama-models`; keep the share and pin replica to 1 |
