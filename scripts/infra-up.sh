#!/usr/bin/env bash
# Recreate the EC2 host after scripts/infra-down.sh and rewire everything that
# hangs off its (new) IP:
#
#   1. terraform apply            — instance, EIP, SG, key pair come back
#   2. wait for cloud-init        — Docker installed, /opt/nl-regex ready
#   3. restore .env               — from infra/terraform/.env.backup, with the
#                                   old <ip>.nip.io host rewritten to the new one
#   4. gh secret DEPLOY_HOST      — updated to the new IP
#   5. frontend/vercel.json       — rewrites pointed at the new nip.io host
#
# The vercel.json change must be committed + pushed (that push IS the deploy).
#
# Usage:  scripts/infra-up.sh
#         SSH_KEY=~/.ssh/nlregex_deploy scripts/infra-up.sh
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/infra/terraform"

terraform apply

IP="$(terraform output -raw instance_public_ip)"
NIP="$(echo "$IP" | tr . -).nip.io"
SSH=(ssh ${SSH_KEY:+-i "$SSH_KEY"} -o StrictHostKeyChecking=accept-new "ubuntu@$IP")

echo "==> Instance up at $IP ($NIP). Waiting for cloud-init (~2 min)..."
for i in $(seq 1 30); do
  if "${SSH[@]}" 'cloud-init status --wait >/dev/null 2>&1 && test -d /opt/nl-regex' 2>/dev/null; then
    break
  fi
  [[ "$i" == 30 ]] && { echo "ERROR: instance never became ready" >&2; exit 1; }
  sleep 10
done

if [[ -f .env.backup ]]; then
  echo "==> Restoring /opt/nl-regex/.env (host rewritten to $NIP)"
  sed -E "s/[0-9]+-[0-9]+-[0-9]+-[0-9]+\.nip\.io/$NIP/g" .env.backup \
    | "${SSH[@]}" 'cat > /opt/nl-regex/.env && chmod 600 /opt/nl-regex/.env'
else
  echo "WARN: no .env.backup found — create /opt/nl-regex/.env by hand before deploying."
fi

echo "==> Updating GitHub secret DEPLOY_HOST"
gh secret set DEPLOY_HOST --body "$IP"

echo "==> Pointing frontend/vercel.json at $NIP"
perl -pi -e "s#http://[^/\"]+#http://$NIP#g" "$REPO_ROOT/frontend/vercel.json"

cat <<EOF

Done. Final step — ship it:

    git add frontend/vercel.json
    git commit -m "chore: point frontend at new backend host"
    git push origin main        # push runs the full deploy pipeline

EOF
