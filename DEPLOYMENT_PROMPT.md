# Prompt: Deploy the RAG-over-Wikipedia System to Azure

> **Status: point-in-time record, 3 Jul 2026.** Kept as evidence of what was
> true then, not maintained against the current code. Where it disagrees with
> the repository, the repository is right. Living references:
> `README.md`, `DEMO_RUNBOOK.md`, `ANNOTATED_CODE.md`, `TESTS_ANNOTATED.md`.

Deploy the existing multi-service app (FastAPI API, React frontend, Qdrant, Ollama) to **Azure Container Apps (ACA)** using the **Azure CLI**. The resource group **already exists** — do not create or delete it. Everything else is created by the steps below. Each step ends with a **Verify** check (test-per-component).

> Best practices applied: managed-identity image pulls (no ACR admin), internal-only ingress for Qdrant/Ollama, HTTPS ingress, ports matched to app listeners, no hardcoded credentials in images.

## Target architecture (Azure)

```
                 Internet (HTTPS)
                       │
        ┌──────────────┴───────────────┐
        ▼                              ▼
  rag-frontend (ACA)  ──/api──▶  rag-api (ACA)        external ingress
                                     │   │
                       ┌─────────────┘   └─────────────┐
                       ▼                                ▼
                  qdrant (ACA)                     ollama (ACA)         internal ingress only
                  Azure Files: data                Azure Files: models   Dedicated D4 profile

  Pull images ← Azure Container Registry (managed identity: AcrPull)
  Logs        → Log Analytics Workspace  |  Persistence → Storage Account (file shares)
```

| Compose service | ACA app | Ingress | Port | Profile | Persistence |
|---|---|---|---|---|---|
| frontend | `rag-frontend` | external | 80 | Consumption | — |
| api | `rag-api` | external | 8000 | Consumption | — |
| qdrant | `qdrant` | internal | 6333 | Consumption | Azure Files `qdrant-data` |
| ollama | `ollama` | internal | 11434 | Dedicated **D4** | Azure Files `ollama-models` |

## Azure resources to create

| Resource | Name (var) | Purpose |
|---|---|---|
| Container Registry | `$ACR` | Store `rag-api` + `rag-frontend` images (admin disabled) |
| User-assigned managed identity | `rag-acr-pull` | `AcrPull` for image pulls |
| Log Analytics workspace | `rag-logs` | ACA logs |
| Container Apps environment | `rag-env` | Hosts all 4 apps; workload profiles enabled |
| Workload profile | `ollama-d4` (D4) | Dedicated compute for Ollama (LLM needs ~8 GB RAM) |
| Storage account + 2 file shares | `$STG` (`qdrant-data`, `ollama-models`) | Persist vectors + model weights |
| 4 Container Apps | see table above | The services |

## Prerequisites

- `az` CLI logged in (`az login`) and the correct subscription selected.
- `backend/Dockerfile` (listens on `8000`) and `frontend/Dockerfile` (nginx on `80`, accepts `--build-arg VITE_API_BASE_URL`) exist from the Compose setup.
- Images are built **in ACR** (`az acr build`) — no local Docker required.

## Variables (PowerShell)

```powershell
$RG    = "<existing-resource-group>"
$LOC   = "eastus"                 # use a region with ACA Dedicated D-series quota
$ACR   = "ragwiki$(Get-Random -Maximum 99999)"   # globally unique, lowercase alnum
$STG   = "ragwikistg$(Get-Random -Maximum 99999)" # globally unique, lowercase alnum
$ENV   = "rag-env"
$LAW   = "rag-logs"
$UAMI  = "rag-acr-pull"
```

## Step 0 — Providers, extension, quota

```powershell
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.Storage --wait
```

**Verify:** `az containerapp env workload-profile list-supported -l $LOC -o table` lists a **D4** profile. If not, pick another region.

## Step 1 — Container Registry + build images

```powershell
az acr create -g $RG -n $ACR --sku Basic --admin-enabled false
az acr build -r $ACR -t rag-api:latest ./backend
# frontend is built AFTER the API exists (needs its URL) — see Step 7
```

**Verify:** `az acr repository show -n $ACR --image rag-api:latest` succeeds.

## Step 2 — Managed identity + AcrPull

```powershell
az identity create -g $RG -n $UAMI
$UAMI_ID  = az identity show -g $RG -n $UAMI --query id -o tsv
$UAMI_PID = az identity show -g $RG -n $UAMI --query principalId -o tsv
$ACR_ID   = az acr show -g $RG -n $ACR --query id -o tsv
az role assignment create --assignee-object-id $UAMI_PID --assignee-principal-type ServicePrincipal --role AcrPull --scope $ACR_ID
```

**Verify:** `az role assignment list --assignee $UAMI_PID --scope $ACR_ID -o table` shows `AcrPull`.

## Step 3 — Log Analytics + ACA environment + workload profile

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

**Verify:** `az containerapp env show -g $RG -n $ENV --query properties.provisioningState -o tsv` returns `Succeeded`.

## Step 4 — Storage account + file shares + env storage links

```powershell
az storage account create -g $RG -n $STG -l $LOC --sku Standard_LRS --kind StorageV2
az storage share-rm create -g $RG --storage-account $STG -n qdrant-data --quota 50
az storage share-rm create -g $RG --storage-account $STG -n ollama-models --quota 50
$STG_KEY = az storage account keys list -g $RG -n $STG --query "[0].value" -o tsv

az containerapp env storage set -g $RG -n $ENV --storage-name qdrant-data `
  --azure-file-account-name $STG --azure-file-account-key $STG_KEY --azure-file-share-name qdrant-data --access-mode ReadWrite
az containerapp env storage set -g $RG -n $ENV --storage-name ollama-models `
  --azure-file-account-name $STG --azure-file-account-key $STG_KEY --azure-file-share-name ollama-models --access-mode ReadWrite
```

**Verify:** `az containerapp env storage list -g $RG -n $ENV -o table` lists both storages.

## Step 5 — Deploy Ollama (internal, persistent) + pull model

```powershell
New-Item -ItemType Directory -Force infra/aca | Out-Null
@"
location: $LOC
name: ollama
type: Microsoft.App/containerApps
properties:
  managedEnvironmentId: $ENV_ID
  workloadProfileName: ollama-d4
  configuration:
    activeRevisionsMode: Single
    ingress: { external: false, targetPort: 11434, transport: http }
  template:
    containers:
      - image: ollama/ollama:latest
        name: ollama
        resources: { cpu: 3.5, memory: 8Gi }
        volumeMounts:
          - { volumeName: models, mountPath: /root/.ollama }
    scale: { minReplicas: 1, maxReplicas: 1 }
    volumes:
      - { name: models, storageType: AzureFile, storageName: ollama-models }
"@ | Set-Content infra/aca/ollama.yaml

az containerapp create -g $RG -n ollama --yaml infra/aca/ollama.yaml
$OLLAMA_FQDN = az containerapp show -g $RG -n ollama --query properties.configuration.ingress.fqdn -o tsv

# One-time model pull into the persistent volume (interactive exec, then run inside):
#   ollama pull llama3.2:3b
az containerapp exec -g $RG -n ollama --command /bin/sh
```

**Verify:** inside the exec session, `ollama list` shows `llama3.2:3b`. The model persists on the file share across restarts.

## Step 6 — Deploy Qdrant (internal, persistent)

```powershell
@"
location: $LOC
name: qdrant
type: Microsoft.App/containerApps
properties:
  managedEnvironmentId: $ENV_ID
  workloadProfileName: Consumption
  configuration:
    activeRevisionsMode: Single
    ingress: { external: false, targetPort: 6333, transport: http }
  template:
    containers:
      - image: qdrant/qdrant:latest
        name: qdrant
        resources: { cpu: 1.0, memory: 2Gi }
        volumeMounts:
          - { volumeName: data, mountPath: /qdrant/storage }
    scale: { minReplicas: 1, maxReplicas: 1 }
    volumes:
      - { name: data, storageType: AzureFile, storageName: qdrant-data }
"@ | Set-Content infra/aca/qdrant.yaml

az containerapp create -g $RG -n qdrant --yaml infra/aca/qdrant.yaml
$QDRANT_FQDN = az containerapp show -g $RG -n qdrant --query properties.configuration.ingress.fqdn -o tsv
```

**Verify:** `az containerapp logs show -g $RG -n qdrant --tail 20` shows Qdrant started with no volume errors.

## Step 7 — Deploy API, then build + deploy frontend, then wire CORS

```powershell
# 7a. API (external), reaches backing services over internal HTTPS ingress
az containerapp create -g $RG -n rag-api --environment $ENV `
  --image "$ACR.azurecr.io/rag-api:latest" `
  --user-assigned $UAMI_ID --registry-server "$ACR.azurecr.io" --registry-identity $UAMI_ID `
  --workload-profile-name Consumption --cpu 1.0 --memory 2Gi --min-replicas 1 --max-replicas 3 `
  --ingress external --target-port 8000 --transport http `
  --env-vars QDRANT_URL="https://$QDRANT_FQDN" OLLAMA_URL="https://$OLLAMA_FQDN" `
             EMBED_MODEL="BAAI/bge-small-en-v1.5" LLM_MODEL="llama3.2:3b" ALLOWED_ORIGINS="*"
$API_FQDN = az containerapp show -g $RG -n rag-api --query properties.configuration.ingress.fqdn -o tsv

# 7b. Build frontend with the API URL baked in, then deploy
az acr build -r $ACR -t rag-frontend:latest --build-arg VITE_API_BASE_URL="https://$API_FQDN" ./frontend
az containerapp create -g $RG -n rag-frontend --environment $ENV `
  --image "$ACR.azurecr.io/rag-frontend:latest" `
  --user-assigned $UAMI_ID --registry-server "$ACR.azurecr.io" --registry-identity $UAMI_ID `
  --workload-profile-name Consumption --cpu 0.5 --memory 1Gi --min-replicas 1 --max-replicas 2 `
  --ingress external --target-port 80 --transport http
$FRONTEND_FQDN = az containerapp show -g $RG -n rag-frontend --query properties.configuration.ingress.fqdn -o tsv

# 7c. Lock CORS down to the real frontend origin
az containerapp update -g $RG -n rag-api --set-env-vars ALLOWED_ORIGINS="https://$FRONTEND_FQDN"
```

**Verify:** `curl https://$API_FQDN/health` returns healthy.

## Step 8 — Ingest data, then end-to-end test

Run the Prefect ingestion against the deployed Qdrant (locally with `QDRANT_URL=https://$QDRANT_FQDN`, or from a one-off job), then:

```powershell
curl https://$API_FQDN/query -Method POST -ContentType "application/json" -Body '{"question":"Who painted the Mona Lisa?"}'
```

## Per-component validation (summary)

| Component | Test |
|---|---|
| ACR | `az acr repository show` lists `rag-api` + `rag-frontend` |
| Managed identity | `AcrPull` present; apps pull images without admin creds |
| ACA env | `provisioningState == Succeeded`; D4 profile present |
| Qdrant | logs clean; collection persists across a `az containerapp revision restart` |
| Ollama | `ollama list` shows `llama3.2:3b` after restart (volume persisted) |
| API | `GET /health` 200; `POST /query` returns grounded answer + citations |
| Frontend | open `https://$FRONTEND_FQDN`, ask a question, see answer + sources |
| End-to-end | refusal returned when no relevant context exists |

## Security requirements

- ACR admin disabled; pulls via user-assigned managed identity (`AcrPull`, scoped to the registry only).
- Qdrant and Ollama use **internal** ingress — never publicly reachable.
- All ingress is HTTPS (ACA-managed certs).
- No secrets baked into images; the storage key is only used for ACA env-storage config. For hardening, move it to Key Vault and mount via managed identity.
- API CORS restricted to the frontend origin (Step 7c).

## Teardown (keep the resource group)

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
```

## Notes / options

- **GPU (optional):** for faster inference, create a GPU workload profile (e.g. `--workload-profile-type NC8as-T4-A100`/`NC24-A100` where available + quota) and point the `ollama` app at it. CPU D4 is the default and is sufficient for `llama3.2:3b`.
- **Azure Files + Qdrant:** SMB latency is acceptable for the tiny/real subset; if it becomes a bottleneck, switch the `qdrant-data` share to Azure Files **Premium (NFS)**.
- **IaC alternative (recommended for repeatability):** the same topology can be expressed as Bicep under `infra/` and deployed with `az deployment group create` (validate first with `what-if`) or `azd up`. This prompt uses the CLI per the explicit request.
