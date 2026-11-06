#!/usr/bin/env bash
# Phase 2: toggle public network access on the MCP App Service. `disable` refuses unless the NCC rule is ESTABLISHED.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/.."
MODE=${1:-}
get() { local v="${!1:-}"; if [ -n "$v" ]; then echo "$v"; else grep "^$1=" .lab.env | cut -d= -f2 || true; fi; }
WEBAPP_ID=$(az webapp show -g rg-mcp-obo-lab -n "$(get MCP_APP_NAME)" --query id -o tsv)
case "$MODE" in
  disable)
    STATE=$(databricks account network-connectivity get-private-endpoint-rule "$(get NCC_ID)" "$(get PE_RULE_ID)" \
      --profile "<your-cli-account-profile>" -o json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('connection_state',''))" 2>/dev/null || true)
    if [ "$STATE" != "ESTABLISHED" ]; then
      echo "refusing to disable public access: private endpoint rule state is '${STATE:-unknown}'"; exit 1
    fi
    VALUE=Disabled ;;
  enable) VALUE=Enabled ;;
  *) echo "usage: $0 disable|enable"; exit 2 ;;
esac
az resource update --ids "$WEBAPP_ID" --set properties.publicNetworkAccess="$VALUE" -o none
echo "publicNetworkAccess = $(az resource show --ids "$WEBAPP_ID" --query properties.publicNetworkAccess -o tsv)"
