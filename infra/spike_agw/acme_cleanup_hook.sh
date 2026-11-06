#!/usr/bin/env bash
# SPIKE: certbot --manual-cleanup-hook. Removes the published HTTP-01 token blob.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
LAB="$(cd "$(dirname "$0")/../.." && pwd)"
SA=$(grep ^AGW_ACME_SA= "$LAB/.lab.env" | cut -d= -f2)
az storage blob delete --account-name "$SA" -c '$web' -n ".well-known/acme-challenge/$CERTBOT_TOKEN" --auth-mode key -o none || true
