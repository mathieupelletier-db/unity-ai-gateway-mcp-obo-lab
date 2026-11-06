#!/usr/bin/env bash
# Atlassian leg: route https://<AGW_FQDN>/v2/* to mcp.atlassian.com through agw-mcp-lab; every other path stays on the lab MCP.
# App Gateway access logs go to Log Analytics as evidence of the egress path. rule-acme/listener80 are untouched.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/../.."
RG=rg-mcp-obo-lab; AGW=agw-mcp-lab; LOC=canadacentral; LAW=law-mcp-lab
G=(-g "$RG" --gateway-name "$AGW")
setenv() { { grep -v "^$1=" .lab.env || true; echo "$1=$2"; } > .lab.env.tmp && mv .lab.env.tmp .lab.env; }

az network application-gateway probe show "${G[@]}" -n probe-atlassian -o none 2>/dev/null || \
  az network application-gateway probe create "${G[@]}" -n probe-atlassian --protocol Https --host mcp.atlassian.com \
    --path /.well-known/oauth-protected-resource/v2/mcp --match-status-codes 200 -o none
az network application-gateway address-pool show "${G[@]}" -n pool-atlassian -o none 2>/dev/null || \
  az network application-gateway address-pool create "${G[@]}" -n pool-atlassian --servers mcp.atlassian.com -o none
az network application-gateway http-settings show "${G[@]}" -n bes-atlassian -o none 2>/dev/null || \
  az network application-gateway http-settings create "${G[@]}" -n bes-atlassian --port 443 --protocol Https \
    --host-name mcp.atlassian.com --probe probe-atlassian --timeout 60 -o none
az network application-gateway url-path-map show "${G[@]}" -n pm-mcp-lab -o none 2>/dev/null || \
  az network application-gateway url-path-map create "${G[@]}" -n pm-mcp-lab --rule-name atlassian-v2 --paths '/v2/*' \
    --address-pool pool-atlassian --http-settings bes-atlassian \
    --default-address-pool appGatewayBackendPool --default-http-settings appGatewayBackendHttpSettings -o none
az network application-gateway rule update "${G[@]}" -n rule1 --rule-type PathBasedRouting --url-path-map pm-mcp-lab -o none

az monitor log-analytics workspace show -g "$RG" -n "$LAW" -o none 2>/dev/null || \
  az monitor log-analytics workspace create -g "$RG" -n "$LAW" -l "$LOC" --retention-time 30 -o none
setenv LAW_CUSTOMER_ID "$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query customerId -o tsv)"
AGW_ID=$(az network application-gateway show -g "$RG" -n "$AGW" --query id -o tsv)
LAW_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query id -o tsv)
az monitor diagnostic-settings show --resource "$AGW_ID" -n diag-agw-access -o none 2>/dev/null || \
  az monitor diagnostic-settings create --resource "$AGW_ID" -n diag-agw-access --workspace "$LAW_ID" \
    --logs '[{"category":"ApplicationGatewayAccessLog","enabled":true}]' -o none

echo "Route ready: https://$(grep ^AGW_FQDN= .lab.env | cut -d= -f2)/v2/mcp -> mcp.atlassian.com"
echo "Rollback: az network application-gateway rule update -g $RG --gateway-name $AGW -n rule1 --rule-type Basic --address-pool appGatewayBackendPool --http-settings appGatewayBackendHttpSettings"
