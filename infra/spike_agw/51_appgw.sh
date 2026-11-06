#!/usr/bin/env bash
# SPIKE (throwaway): public IP (DNS label) + self-signed cert + App Gateway v2 (HTTPS 443 → MCP App Service via our PE).
# Secrets: PFX password in .lab.secrets (AGW_PFX_PASSWORD); key/PFX under build/agw (gitignored, mode 600).
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/../.."
umask 077
RG=rg-mcp-obo-lab; LOC=canadacentral; VNET=vnet-mcp-lab; AGW=agw-mcp-lab; PIP=pip-mcp-lab-agw
APP=$(grep ^MCP_APP_NAME= .lab.env | cut -d= -f2)
setenv() { { grep -v "^$1=" "$3" || true; echo "$1=$2"; } > "$3.tmp" && mv "$3.tmp" "$3"; }

LABEL=$(grep ^AGW_DNS_LABEL= .lab.env | cut -d= -f2 || true)
LABEL=${LABEL:-mcplabagw-$(python3 -c "import secrets; print(secrets.token_hex(2))")}
setenv AGW_DNS_LABEL "$LABEL" .lab.env
az network public-ip show -g "$RG" -n "$PIP" -o none 2>/dev/null || \
  az network public-ip create -g "$RG" -n "$PIP" -l "$LOC" --sku Standard --allocation-method Static --dns-name "$LABEL" -o none
FQDN=$(az network public-ip show -g "$RG" -n "$PIP" --query dnsSettings.fqdn -o tsv)
setenv AGW_FQDN "$FQDN" .lab.env

PW=$(grep ^AGW_PFX_PASSWORD= .lab.secrets | cut -d= -f2- || true)
if [ -z "$PW" ]; then PW=$(python3 -c "import secrets; print(secrets.token_hex(16))"); setenv AGW_PFX_PASSWORD "$PW" .lab.secrets; fi
mkdir -p build/agw
openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj "/CN=$FQDN" -addext "subjectAltName=DNS:$FQDN" \
  -keyout build/agw/selfsigned.key -out build/agw/selfsigned.crt 2>/dev/null
openssl pkcs12 -export -inkey build/agw/selfsigned.key -in build/agw/selfsigned.crt -out build/agw/selfsigned.pfx -passout "pass:$PW"

if ! az network application-gateway show -g "$RG" -n "$AGW" -o none 2>/dev/null; then
  az network application-gateway create -g "$RG" -n "$AGW" -l "$LOC" --sku Standard_v2 --capacity 1 \
    --vnet-name "$VNET" --subnet snet-appgw --public-ip-address "$PIP" \
    --frontend-port 443 --http-settings-protocol Https --http-settings-port 443 --http-settings-cookie-based-affinity Disabled \
    --cert-file build/agw/selfsigned.pfx --cert-password "$PW" \
    --servers "$APP.azurewebsites.net" --priority 100 -o none
fi
az network application-gateway probe create -g "$RG" --gateway-name "$AGW" -n probe-mcp --protocol Https --path /mcp \
  --host-name-from-http-settings true --match-status-codes 200-401 --interval 30 --timeout 30 --threshold 3 -o none 2>/dev/null || true
az network application-gateway http-settings update -g "$RG" --gateway-name "$AGW" -n appGatewayBackendHttpSettings \
  --host-name-from-backend-pool true --probe probe-mcp --timeout 60 -o none

echo "AGW_FQDN=$FQDN"
az network application-gateway show-backend-health -g "$RG" -n "$AGW" \
  --query "backendAddressPools[0].backendHttpSettingsCollection[0].servers[0].[address, health]" -o tsv
