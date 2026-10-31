#!/usr/bin/env bash
# Deploy (or redeploy) KhetDrishti to a VM over SSH.   Usage:  bash scripts/deploy_vm.sh <public-ip>
#
# Ships the committed code (git archive, so nothing untracked/secret leaks), a server env file containing only
# the variables the app needs, then builds and starts the stack. The app is served at https://<ip-dashed>.sslip.io
set -euo pipefail

IP="${1:?usage: deploy_vm.sh <public-ip>}"
KEY="${SSH_KEY:-$HOME/.ssh/khetdrishti_oracle}"
USER_AT="${SSH_USER:-ubuntu}@$IP"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
SITE="$(echo "$IP" | tr . -).sslip.io"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> bootstrap server (idempotent)"
$SSH "$USER_AT" 'bash -s' < deploy/server_bootstrap.sh

echo "==> ship code (HEAD of git) + server env"
git archive --format=tar HEAD | $SSH "$USER_AT" 'rm -rf /opt/khetdrishti/app && mkdir -p /opt/khetdrishti/app && tar -x -C /opt/khetdrishti/app'
grep -E '^(GROQ_API_KEYS|GROQ_MODELS|GROQ_STT_MODEL|DATA_GOV_IN_API_KEY|CDSE_CLIENT_ID|CDSE_CLIENT_SECRET|SARVAM_API_KEY|TELEGRAM_BOT_TOKEN|TELEGRAM_DEFAULT_CHAT_ID|CONTACT_UA|MONITOR_HOUR_IST)=' .env \
  | $SSH "$USER_AT" 'umask 077 && cat > /opt/khetdrishti/app/.env.server'
echo "SITE_ADDRESS=$SITE" | $SSH "$USER_AT" 'cat > /opt/khetdrishti/app/deploy/.env'

echo "==> build & start (first build takes a few minutes)"
$SSH "$USER_AT" 'cd /opt/khetdrishti/app/deploy && sudo docker compose up -d --build --remove-orphans && sudo docker image prune -f >/dev/null'

echo "==> waiting for https://$SITE"
for i in $(seq 1 40); do
  if curl -fsS -m 10 "https://$SITE/api/crops" >/dev/null 2>&1; then
    echo "LIVE: https://$SITE"
    exit 0
  fi
  sleep 6
done
echo "Not reachable yet. Check: $SSH $USER_AT 'cd /opt/khetdrishti/app/deploy && sudo docker compose logs --tail 50'"
exit 1
