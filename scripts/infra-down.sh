#!/usr/bin/env bash
# Tear down the EC2 host (instance, EIP, security group, key pair) while
# KEEPING the S3 bucket, IAM, and budget — compute is the only real cost, so
# this takes the bill to ~zero between demo sessions.
#
# The box's disk dies with it, so /opt/nl-regex/.env is backed up to
# infra/terraform/.env.backup (gitignored) first. Bring everything back with
# scripts/infra-up.sh.
#
# Usage:  scripts/infra-down.sh
#         SSH_KEY=~/.ssh/nlregex_deploy scripts/infra-down.sh   # non-default key
set -euo pipefail
cd "$(dirname "$0")/../infra/terraform"

IP="$(terraform output -raw instance_public_ip 2>/dev/null || true)"
if [[ -n "$IP" && "$IP" != "null" ]]; then
  echo "==> Backing up /opt/nl-regex/.env from $IP"
  scp ${SSH_KEY:+-i "$SSH_KEY"} -o StrictHostKeyChecking=accept-new \
    "ubuntu@$IP:/opt/nl-regex/.env" .env.backup \
    || echo "WARN: .env backup failed — continuing (keep your own copy!)"
else
  echo "==> No instance in state; nothing to back up."
fi

# -var on the CLI overrides terraform.tfvars: an empty ssh_public_key gates
# every EC2 resource off (see ec2.tf), so this destroys ONLY the host stack.
echo "==> Destroying the EC2 host (bucket/IAM/budget untouched)"
terraform apply -var ssh_public_key=""

echo "==> Done. Recreate later with scripts/infra-up.sh"
