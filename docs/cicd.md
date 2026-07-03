# CI/CD & the live deployment

How this project actually ships: GitHub Actions builds and tests on every push,
then deploys the backend stack to an ARM EC2 host and the frontend to Vercel.
For platform-agnostic deployment guidance see [deployment.md](deployment.md);
this doc describes the concrete, wired-up setup.

## Topology

```
            ┌────────────────────────┐
 browser ──►│  Vercel (free, HTTPS)  │        static SPA + robots.txt
            │  nl-regex-processor    │
            │  .vercel.app           │
            └───────────┬────────────┘
                        │ rewrite /api/* and /healthz (server-side proxy)
                        ▼
            ┌────────────────────────┐
            │  EC2 t4g.large (ARM)   │  gunicorn :80 ◄─ Host filter (nip.io)
            │  docker compose:       │
            │   backend │ worker │ redis
            └─────┬──────────┬───────┘
                  │          │
        Neon (Postgres)   S3 bucket ◄──── browser, directly (presigned PUT/GET)
                  │          ▲
             Anthropic    Spark s3a:// + DuckDB httpfs
```

- The SPA calls relative `/api/...`; Vercel's rewrite proxies those requests to
  the EC2 host **server-side**, so the HTTPS page can talk to the HTTP-only
  backend with no mixed-content problem, no CORS, and no TLS certificate on the
  instance.
- Large files never pass through Vercel or gunicorn — the browser uploads
  straight to S3 with presigned URLs (which is why the bucket's CORS allows the
  Vercel origin).
- The backend containers get AWS credentials from the **instance profile** via
  IMDS — no static keys on the box. (Terraform sets the IMDS hop limit to 2;
  the default of 1 is unreachable from inside a container.)

## The pipeline

One workflow, [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml):

| Trigger | What runs |
|---------|-----------|
| pull request | tests only |
| push to `main` | tests → build/push → deploy (both halves) |
| manual (`workflow_dispatch`) | same as a `main` push — use after `scripts/infra-up.sh` |

| Job | Runner | What it does |
|-----|--------|--------------|
| `backend-tests` | `ubuntu-24.04-arm` | Builds the backend image (native arm64 — same architecture as the t4g host, no QEMU), then runs pytest **inside it** against a Neon test database and a Redis service container. The checkout is bind-mounted over `/app` because the production image deliberately excludes `tests/`. |
| `frontend-checks` | `ubuntu-latest` | `npm ci` + `tsc` over both tsconfigs + `vite build`. |
| `build-push` | `ubuntu-24.04-arm` | Pushes `ghcr.io/cosykid/nl-regex-processor-backend` tagged `latest` + the commit sha, reusing the test job's layer cache. |
| `deploy-backend` | `ubuntu-latest` | `scp`s `docker-compose.prod.yml` to `/opt/nl-regex/`, SSHes in, logs into GHCR with the run's ephemeral `GITHUB_TOKEN`, pins `IMAGE_TAG=<sha>` into the server `.env`, `docker compose pull && up -d`, prunes old images, then polls `/healthz` (with the nip.io Host header) until healthy. |
| `deploy-frontend` | `ubuntu-latest` | `vercel pull/build/deploy --prebuilt --prod`. |

Pinning `IMAGE_TAG` to the sha (rather than deploying `:latest`) means a later
manual `docker compose up -d` on the box re-runs the same build, and rollback
is just editing that one line.

### Repository secrets

| Secret | Value |
|--------|-------|
| `DEPLOY_HOST` | The instance's Elastic IP (`terraform output -raw instance_public_ip`). |
| `DEPLOY_SSH_KEY` | Private key matching the `ssh_public_key` Terraform variable. |
| `TEST_DATABASE_URL` | Neon connection string for CI tests — **direct** endpoint, not the pooler (test-DB creation is unreliable through the pooler; see `pytest.ini`). |
| `VERCEL_TOKEN` / `VERCEL_ORG_ID` / `VERCEL_PROJECT_ID` | From vercel.com → Tokens, and `frontend/.vercel/project.json` after `vercel link`. |

## Infrastructure (Terraform)

`infra/terraform/` provisions everything; two opt-in gates keep partial applies
safe:

- **`ssh_public_key` non-empty** creates the EC2 host (`ec2.tf`): t4g.large,
  Ubuntu 24.04 arm64, Elastic IP, security group (80 + 22), the existing app
  instance profile, 30 GB gp3 root, 2 GiB swap, Docker installed by cloud-init
  into `/opt/nl-regex`. Empty (the default) = no instance, so bucket-only
  applies keep working.
- **`budget_alert_email` non-empty** creates the spend guardrails (`budget.tf`,
  next section).

Runtime secrets live only in `/opt/nl-regex/.env` on the box (mode 600) — the
pipeline never carries them. See the header comment in
[`docker-compose.prod.yml`](../docker-compose.prod.yml) for the expected keys.

## Spend guardrails (and how the auto-stop works)

`budget.tf` creates a monthly **AWS Budget** (`budget_limit_usd`, default 100)
plus an automatic stop action. The chain, end to end:

1. AWS's billing pipeline aggregates the account's month-to-date cost. The
   budget measures **gross** spend (`include_credit = false`): promotional
   credits normally absorb charges so the *net* bill reads $0 right up until
   they run out — gross is the number that actually eats the credit balance.
2. AWS Budgets re-evaluates whenever new cost data lands and sends email at
   **50%** actual, **80%** actual, **100% forecasted** (trending to exceed the
   month), and **100%** actual.
3. At **90% actual**, the budget *action* fires (`approval_model = AUTOMATIC`,
   so no human confirmation step): Budgets assumes the
   `…-budget-stop` IAM role (trusts `budgets.amazonaws.com`; permissions
   scoped to stopping this one instance) and runs an AWS-owned SSM Automation
   that calls `ec2:StopInstances` on the app instance.
4. The instance **stops** — compute billing ends immediately. It is *not*
   terminated: the disk (and `/opt/nl-regex/.env`), the Elastic IP, and the
   association all survive. Starting it again from the console brings the
   whole stack back automatically (`restart: unless-stopped`) on the **same
   IP** — no redeploy, no rewiring.

Honest limitations — this is a guardrail, not a hard cap:

- **Billing data lags.** Cost data refreshes a few times a day (typically
  8–24 h behind), and Budgets evaluates on that cadence. A fast spending burst
  can overshoot before it's detected; the 90% trigger (rather than 100%) exists
  to buy headroom for exactly that lag.
- **Fires once per month.** After triggering, the action resets at the next
  budget period. If you manually restart the instance in the same month, it
  will not be stopped again automatically.
- **Stop ≠ $0.** The 30 GB EBS volume plus the Elastic IP keep billing
  (~US$6.5/month combined). `scripts/infra-down.sh` is the true-zero option.
- **AWS only, whole account.** The budget has no cost filter (any AWS usage
  counts toward it — which is what you want for credit protection), but
  Anthropic, Vercel, and Neon spend are separate systems with their own limits.

## Bot & abuse hardening

The API is public and token-less; every job POST is a potential LLM call plus
a Spark run, so four cheap layers keep drive-by traffic out:

| Layer | Mechanism |
|-------|-----------|
| Host filter | Vercel's rewrite targets `http://<dashed-ip>.nip.io` (wildcard DNS that resolves to the EIP), and `DJANGO_ALLOWED_HOSTS` admits only that name (+ `localhost` for the container healthcheck). Scanners hitting the raw IP send `Host: <ip>` and get a 400 before any DB/S3/LLM work. |
| Rate limit | DRF `AnonRateThrottle` keyed per client IP (`NUM_PROXIES: 1` — one trusted proxy, Vercel, in front), stored in Redis. The rate comes from `API_ANON_THROTTLE` (prod default `60/min`); unset = disabled, so dev and the test suite run unthrottled. |
| Slow clients | gunicorn runs internet-facing with no nginx buffer, so prod sets `GUNICORN_CMD_ARGS="--worker-class gthread --threads 8 --keep-alive 5"` — threaded workers and a short keep-alive stop idle/slow connections from pinning the worker pool. |
| Crawlers | `frontend/public/robots.txt` disallows everything (demo app, nothing to index). |

## Operations

| Task | How |
|------|-----|
| Pause everything (bill → ~zero) | `scripts/infra-down.sh` — backs up the server `.env` to `infra/terraform/.env.backup` (gitignored), then destroys instance/EIP/SG/key pair. Bucket, IAM, and budget survive. |
| Resume | `scripts/infra-up.sh` — re-applies, waits for cloud-init, restores `.env` (rewriting the old nip.io host to the new IP), updates the `DEPLOY_HOST` secret, points `frontend/vercel.json` at the new host. Then commit + push (that push is the deploy). |
| Redeploy without a code change | Actions → CI/CD → *Run workflow*. |
| Roll back | SSH in, set `IMAGE_TAG=<old sha>` in `/opt/nl-regex/.env`, `docker compose -f /opt/nl-regex/docker-compose.yml up -d`. Or revert the commit and push. |
| Restart after a budget stop | EC2 console → start instance. Same IP, containers auto-start; nothing else to do. |
| Tune the rate limit | Edit `API_ANON_THROTTLE` in `/opt/nl-regex/.env` (e.g. `120/min`), then `docker compose up -d`. |

Running cost while up (ap-southeast-2): ~US$62/mo instance + ~$3 EBS + ~$4
public IPv4 ≈ **$69/month** — hence the pause/resume scripts.
