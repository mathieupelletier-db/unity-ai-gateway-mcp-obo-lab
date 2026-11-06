#!/usr/bin/env bash
# SPIKE (throwaway): serve Let's Encrypt HTTP-01 challenges for the App Gateway hostname.
# Storage static website ($web) as backend + port-80 listener on the App Gateway routing to it.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/../.."
RG=rg-mcp-obo-lab; LOC=canadacentral; AGW=agw-mcp-lab
setenv() { { grep -v "^$1=" .lab.env || true; echo "$1=$2"; } > .lab.env.tmp && mv .lab.env.tmp .lab.env; }

SA=$(grep ^AGW_ACME_SA= .lab.env | cut -d= -f2 || true)
SA=${SA:-stmcplabacme$(python3 -c "import secrets; print(secrets.token_hex(3))")}
setenv AGW_ACME_SA "$SA"
az storage account show -g "$RG" -n "$SA" -o none 2>/dev/null || \
  az storage account create -g "$RG" -n "$SA" -l "$LOC" --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 -o none
az storage blob service-properties update --account-name "$SA" --static-website --index-document index.html --auth-mode key -o none
printf 'ok\n' > build/agw/index.html
az storage blob upload --account-name "$SA" -c '$web' -n index.html -f build/agw/index.html --content-type text/plain --overwrite --auth-mode key -o none
WEB_HOST=$(az storage account show -g "$RG" -n "$SA" --query primaryEndpoints.web -o tsv | sed -e 's#^https://##' -e 's#/$##')
echo "static website host: $WEB_HOST"

FE_IP=$(az network application-gateway frontend-ip list -g "$RG" --gateway-name "$AGW" --query "[0].name" -o tsv)
az network application-gateway frontend-port show -g "$RG" --gateway-name "$AGW" -n port80 -o none 2>/dev/null || \
  az network application-gateway frontend-port create -g "$RG" --gateway-name "$AGW" -n port80 --port 80 -o none
az network application-gateway address-pool show -g "$RG" --gateway-name "$AGW" -n pool-acme -o none 2>/dev/null || \
  az network application-gateway address-pool create -g "$RG" --gateway-name "$AGW" -n pool-acme --servers "$WEB_HOST" -o none
az network application-gateway http-settings show -g "$RG" --gateway-name "$AGW" -n settings-acme -o none 2>/dev/null || \
  az network application-gateway http-settings create -g "$RG" --gateway-name "$AGW" -n settings-acme --port 443 --protocol Https \
    --host-name-from-backend-pool true --timeout 30 -o none
az network application-gateway http-listener show -g "$RG" --gateway-name "$AGW" -n listener80 -o none 2>/dev/null || \
  az network application-gateway http-listener create -g "$RG" --gateway-name "$AGW" -n listener80 --frontend-port port80 --frontend-ip "$FE_IP" -o none
az network application-gateway rule show -g "$RG" --gateway-name "$AGW" -n rule-acme -o none 2>/dev/null || \
  az network application-gateway rule create -g "$RG" --gateway-name "$AGW" -n rule-acme --http-listener listener80 \
    --address-pool pool-acme --http-settings settings-acme --rule-type Basic --priority 200 -o none

FQDN=$(grep ^AGW_FQDN= .lab.env | cut -d= -f2)
echo "check: http://$FQDN/ -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://$FQDN/")"
