# The accounts and credentials this needs, in order

> Companion to [online-deployment.md](online-deployment.md). Everything here is
> done by the owner in a browser or a terminal **he** controls: accounts need an
> identity and a card, and no assistant should hold them.
>
> **Never paste a secret into a chat, a commit, or an issue.** Every value below
> has a place to live: a `.env` on the box, a GitHub secret, a Modal secret.

## What already exists (checked 1 Oct 2026)

| | State |
|---|---|
| **Modal** | an authenticated CLI profile `hadijamalahmad` in `~/.modal.toml` - the account exists |
| **Oracle SSH key** | `~/.ssh/jobhack_oracle` + `.pub`, made 28 Sep for JobHack: **reuse it**, no new key needed |
| **Cloudflare** | nothing found locally |
| **A domain** | nothing found locally |

So the work is: an Oracle instance, a domain on Cloudflare, two Modal secrets,
and the GitHub secrets - in that order, because each step needs the one before.

---

## 1. Oracle Cloud: the always-on box (~30 min, €0)

1. **Account.** <https://cloud.oracle.com> → Start for free. The **home region
   cannot be changed afterwards**: pick an EU one you will keep (Frankfurt,
   Paris, Amsterdam, Marseille, Madrid, Milan, Zurich, Stockholm).
2. **Upgrade to Pay As You Go** once the account exists (Billing → Upgrade).
   You stay at €0 inside the Always Free shapes; what the upgrade buys is
   **capacity**: free-tier accounts are regularly refused A1 capacity
   ("Out of host capacity"), PAYG accounts far less often.
3. **Create the instance.** Compute → Instances → Create:
   - Image **Ubuntu 24.04 (aarch64)**, shape **VM.Standard.A1.Flex**.
   - **4 OCPU / 24 GB** - the whole Always Free A1 allowance in one machine.
     (Free-only accounts get 2/12; both work, 4/24 is twice the prefill speed.)
   - Boot volume 50 GB is plenty: the corpus is 332 MB.
   - **SSH key: paste `~/.ssh/jobhack_oracle.pub`** (the one you already have).
   - Note the **public IP** when it finishes.
4. **Open 80 and 443 - in two places.** This is the classic Oracle trap:
   - VCN → Security Lists → Default → Ingress: allow `0.0.0.0/0` on TCP 80 and 443.
   - **And on the machine itself**, because Oracle's Ubuntu images ship iptables
     rules that drop everything else:
     ```bash
     ssh -i ~/.ssh/jobhack_oracle ubuntu@<public-ip>
     sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
     sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
     sudo netfilter-persistent save
     ```
   - Nothing else is opened. Qdrant, Redis and Ollama stay on the compose network.
5. **Docker:**
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker ubuntu && exit     # log back in
   ```

**What to send me afterwards:** the public IP and the region. Not the private key.

---

## 2. A domain, and Cloudflare DNS (~20 min, €10-15/year - the only cash cost)

1. **Buy the domain.** Cloudflare Registrar sells at cost (no markup, no
   first-year trick) but only for domains already in a Cloudflare account; any
   registrar works. A `.org`/`.dev`/`.app` is ~€10-15/year.
2. **Add it to Cloudflare** (free plan) and point the registrar's nameservers at
   the two Cloudflare gives you.
3. **DNS record:** `A` record, name `rag` (or `@`), value the Oracle public IP.
4. **Grey cloud it - proxy OFF.** Caddy gets its certificate over HTTP-01, which
   Cloudflare's orange-cloud proxy intercepts. Turn the proxy off for this
   record, at least until the certificate is issued. (With the proxy on you need
   Full (strict) plus an origin certificate - more moving parts for no gain
   here.)

**What this gives the deployment:** `DOMAIN=rag.yourdomain.org` in
`infra/oracle/.env`.

---

## 3. Cloudflare Pages + R2 (~15 min, €0)

- **Pages** hosts the built frontend. Either connect the GitHub repo (build
  command `npm run build`, output `dist`, root `projects/rag-wikipedia/frontend`)
  or upload `dist/` by hand the first time. The URL it gives you is what goes in
  `ALLOWED_ORIGINS`.
- **R2** holds the 328 MB corpus snapshot. Create a bucket `rag-corpus`, then
  **R2 → Manage API tokens → Create**, scoped to that bucket, read+write. Keep
  the account id, access key id and secret.
  - A public-read bucket URL (or a signed link) is what `SNAPSHOT_URL` points
    at; Qdrant pulls the file itself.
  - A GitHub release asset on the project repo works too, and needs no R2 at
    all. Use that if you prefer fewer accounts.

---

## 4. Modal: the GPU and the worker (~10 min, €0 within the credit)

The account exists. What is missing is a **token for CI** and the **secrets the
apps read**:

```bash
# A token dedicated to GitHub Actions, so revoking it does not log you out:
modal token new --profile ci          # prints a token id and secret ONCE

# The secrets the two apps read (values never typed into a chat):
modal secret create rag-inference GATEWAY_TOKEN=$(openssl rand -hex 32)
modal secret create rag-worker      WORKER_WAKE_TOKEN=$(openssl rand -hex 32)
```

Then, in the Modal dashboard → Settings → **Usage and billing**: set a
**workspace spend limit** equal to the monthly credit. That is the stop the
watchdog cannot be relied on for - a limit Modal enforces itself.

---

## 5. GitHub secrets - and the one obstacle

The watchdog workflow needs `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` as
**repository secrets**, plus the optional variable `MODAL_DAILY_CEILING_USD`.

**Setting secrets requires Admin on the repository, and this account has Write**
(`David-Mavrodiev/rag-wikipedia-project`). So one of:

- **Ask David** to add the two secrets (he does not need to see the GPU account:
  the token can be revoked any time), or
- **Run the watchdog from your own fork**, where you are the admin, or
- **Skip CI for now**: the workflow already exits quietly when Modal is not
  configured, and the Modal **spend limit** from §4 still protects the account.

```bash
# once you have admin somewhere:
gh secret set MODAL_TOKEN_ID     --repo <owner>/<repo>
gh secret set MODAL_TOKEN_SECRET --repo <owner>/<repo>
gh variable set MODAL_DAILY_CEILING_USD --body "1.00" --repo <owner>/<repo>
```

---

## 6. The order, and what unblocks what

| Step | Unblocks |
|---|---|
| 1. Oracle instance | **phase 0** - the measurement the whole router design waits on |
| 2. Domain + DNS | phase 1 - the API on HTTPS |
| 3. Pages + R2 | phase 2 - the public URL, and the snapshot restore |
| 4. Modal token + secrets | phases 3-4 - GPU answers, and evaluation off the laptop |
| 5. GitHub secrets | phase 5 - the daily bill check |

**Phase 0 needs only step 1.** The moment there is an IP and a key, the first
real number arrives: what llama3.2:3b costs per answer on Ampere cores with a
3,000-token context. Everything about the router is tuned from it.

## 7. What I will never ask for

The SSH private key, the Modal token secret, the R2 secret, or any password.
Give me the **public** IP, the region, the domain name, and the Pages URL; the
secrets go into `.env` on the box and into GitHub/Modal secret stores, where I
can reference them by name without reading them.
