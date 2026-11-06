#!/usr/bin/env bash
# SPIKE (throwaway): swap the App Gateway HTTPS listener cert between the self-signed and Let's Encrypt PFX (A/B/A test).
# Note: the PFX password is passed on argv to az (no stdin option); single-user laptop only.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/../.."
case "${1:-}" in
  selfsigned) PFX=build/agw/selfsigned.pfx ;;
  le) PFX=build/agw/le.pfx ;;
  *) echo "usage: $0 selfsigned|le"; exit 2 ;;
esac
RG=rg-mcp-obo-lab; AGW=agw-mcp-lab
FQDN=$(grep ^AGW_FQDN= .lab.env | cut -d= -f2)
PW=$(grep ^AGW_PFX_PASSWORD= .lab.secrets | cut -d= -f2-)
CERT_NAME=$(az network application-gateway ssl-cert list -g "$RG" --gateway-name "$AGW" --query "[0].name" -o tsv)
az network application-gateway ssl-cert update -g "$RG" --gateway-name "$AGW" -n "$CERT_NAME" --cert-file "$PFX" --cert-password "$PW" -o none
echo "$(date -u +%H:%M:%SZ) listener now serving:"
echo | openssl s_client -connect "$FQDN:443" -servername "$FQDN" 2>/dev/null | openssl x509 -noout -issuer -subject -enddate
