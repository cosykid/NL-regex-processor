# Terraform — AWS infrastructure for the NL-Regex processor

Provisions the whole AWS side: a locked-down **S3 bucket** with
**least-privilege IAM**, the **EC2 host** the backend stack deploys to, and
**spend guardrails** (an AWS Budget with an automatic instance stop).

The app authenticates with the **default AWS credential provider chain**, so the
*same* container talks to S3 via static keys locally and via an IAM role when
deployed — no code changes between the two.

## What it creates

| Resource | Purpose |
|----------|---------|
| `aws_s3_bucket.files` | Uploads (`uploads/`) + Spark results (`results/`). |
| Ownership controls, public-access block | ACLs disabled; **all** public access blocked. |
| Versioning + SSE + lifecycle | Version history, encryption at rest, and cleanup of failed multipart uploads / old versions. |
| `aws_iam_policy.bucket_access` | Least-privilege read/write **to this bucket only**. |
| `aws_iam_role.app` (+ instance profile) | Runtime identity for the **deployed** app (ECS/EC2/EKS). |
| `aws_iam_user.dev` (+ access key) | Static keys for **local** development. Toggle with `create_dev_user`. |
| `aws_instance.app` + EIP + SG + key pair (`ec2.tf`) | The Docker Compose host (t4g.large, arm64, Docker via cloud-init, IMDS hop limit 2 so containers reach the role). **Created only when `ssh_public_key` is set.** |
| `aws_budgets_budget.monthly` + stop action (`budget.tf`) | Gross-spend budget with email alerts (50/80/100%) and an automatic instance **stop at 90%**. **Created only when `budget_alert_email` is set.** |

The CI/CD pipeline that deploys onto the EC2 host — and the full explanation of
the budget auto-stop chain and its limits — is documented in
[docs/cicd.md](../../docs/cicd.md).

## Usage

```bash
cd infra/terraform
terraform init
terraform apply          # review the plan, then confirm
```

Wire the app to the new bucket (local dev):

```bash
# See exactly what to add to backend/.env:
terraform output dotenv_snippet

# The key values are sensitive, so read them explicitly:
terraform output -raw dev_access_key_id
terraform output -raw dev_secret_access_key
```

Add to `backend/.env`:

```
STORAGE_BACKEND=s3
S3_BUCKET=<terraform output -raw bucket_name>
S3_REGION=ap-southeast-2
AWS_REGION=ap-southeast-2
AWS_ACCESS_KEY_ID=<dev_access_key_id>
AWS_SECRET_ACCESS_KEY=<dev_secret_access_key>
```

Then restart the stack — uploads and results now live in S3.

## Deploying to AWS (no static keys)

For a deployed environment, set `create_dev_user = false` and attach the role to
your compute instead of shipping keys:

- **ECS** — set the task role to `app_role_arn`.
- **EC2** — attach `app_instance_profile_name` to the instance.
- **EKS** — set `oidc_provider_arn` + `oidc_subjects` (IRSA) and annotate the
  service account with `app_role_arn`.

The app only needs `STORAGE_BACKEND=s3`, `S3_BUCKET`, and `S3_REGION` — the
credential chain resolves the role automatically.

## State

State is **local** by default (zero prerequisites). For team/prod use, create a
state bucket + enable the `backend "s3"` block in `versions.tf`, then
`terraform init -migrate-state`.

## Teardown

Two levels:

```bash
# Pause: destroy ONLY the EC2 host (instance, EIP, SG, key pair) — the bucket,
# IAM, and budget survive. Backs up the server .env first. Compute is the only
# real cost, so this takes the bill to ~zero between sessions.
../../scripts/infra-down.sh        # resume later with ../../scripts/infra-up.sh

# Everything:
terraform destroy
```

The bucket must be empty unless `force_destroy = true` (dev only).
