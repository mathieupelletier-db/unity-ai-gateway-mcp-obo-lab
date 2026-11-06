#!/usr/bin/env bash
# SPIKE: certbot --manual-auth-hook. Publishes the HTTP-01 token via the storage static website behind App Gateway :80.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
LAB="$(cd "$(dirname "$0")/../.." && pwd)"
SA=$(grep ^AGW_ACME_SA= "$LAB/.lab.env" | cut -d= -f2)
FQDN=$(grep ^AGW_FQDN= "$LAB/.lab.env" | cut -d= -f2)
printf '%s' "$CERTBOT_VALIDATION" > "$LAB/build/agw/acme-$CERTBOT_TOKEN"
az storage blob upload --account-name "$SA" -c '$web' -n ".well-known/acme-challenge/$CERTBOT_TOKEN" \
  -f "$LAB/build/agw/acme-$CERTBOT_TOKEN" --content-type text/plain --overwrite --auth-mode key -o none
for i in $(seq 1 30); do
  [ "$(curl -s --max-time 10 "http://$FQDN/.well-known/acme-challenge/$CERTBOT_TOKEN")" = "$CERTBOT_VALIDATION" ] && exit 0
  sleep 5
done
echo "challenge not reachable through App Gateway :80" >&2; exit 1
