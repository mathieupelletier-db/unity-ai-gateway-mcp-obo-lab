#!/usr/bin/env bash
# SPIKE (throwaway): Let's Encrypt cert for the App Gateway's cloudapp.azure.com name (HTTP-01 via 52_acme_backend.sh),
# then swap it onto the HTTPS listener in place of the self-signed cert. Note: issuance is logged in public CT logs.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/../.."
umask 077
RG=rg-mcp-obo-lab; AGW=agw-mcp-lab
FQDN=$(grep ^AGW_FQDN= .lab.env | cut -d= -f2)
PW=$(grep ^AGW_PFX_PASSWORD= .lab.secrets | cut -d= -f2-)
HOOKS="$(pwd)/infra/spike_agw"
certbot certonly --manual --preferred-challenges http -d "$FQDN" \
  --manual-auth-hook "$HOOKS/acme_auth_hook.sh" --manual-cleanup-hook "$HOOKS/acme_cleanup_hook.sh" \
  --agree-tos --register-unsafely-without-email --non-interactive \
  --config-dir build/certbot/config --work-dir build/certbot/work --logs-dir build/certbot/logs
LIVE="build/certbot/config/live/$FQDN"
openssl pkcs12 -export -inkey "$LIVE/privkey.pem" -in "$LIVE/fullchain.pem" -out build/agw/le.pfx -passout "pass:$PW"
CERT_NAME=$(az network application-gateway ssl-cert list -g "$RG" --gateway-name "$AGW" --query "[0].name" -o tsv)
az network application-gateway ssl-cert update -g "$RG" --gateway-name "$AGW" -n "$CERT_NAME" --cert-file build/agw/le.pfx --cert-password "$PW" -o none
echo "Swapped $CERT_NAME to Let's Encrypt cert for $FQDN"
echo | openssl s_client -connect "$FQDN:443" -servername "$FQDN" 2>/dev/null | openssl x509 -noout -issuer -subject -enddate
