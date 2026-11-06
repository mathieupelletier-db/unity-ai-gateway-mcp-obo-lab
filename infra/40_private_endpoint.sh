#!/usr/bin/env bash
# Phase 2: NCC (canadacentral) attached to the lab workspace + private endpoint rule to the MCP App Service.
# Idempotent: reuses NCC_ID / PE_RULE_ID from .lab.env. Approves the pending Azure connection itself.
# WARNING: this script IS the cutover. Approving an App Service private endpoint flips the app's
# publicNetworkAccess to Disabled (Azure default), so direct callers get 403 from that moment.
# Rollback: bash infra/41_public_access.sh enable
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/.."
ACCT="<your-cli-account-profile>"
RG=rg-mcp-obo-lab
NCC_NAME=ncc-mcp-obo-lab
get() { grep "^$1=" .lab.env | cut -d= -f2 || true; }
setenv() { { grep -v "^$1=" .lab.env || true; echo "$1=$2"; } > .lab.env.tmp && mv .lab.env.tmp .lab.env; }

WS_ID=$(get WORKSPACE_ID)
WEBAPP_ID=$(az webapp show -g "$RG" -n "$(get MCP_APP_NAME)" --query id -o tsv)

NCC_ID=$(get NCC_ID)
if [ -z "$NCC_ID" ]; then
  NCC_ID=$(databricks account network-connectivity create-network-connectivity-configuration "$NCC_NAME" canadacentral \
    --profile "$ACCT" -o json | python3 -c "import json,sys; print(json.load(sys.stdin)['network_connectivity_config_id'])")
  setenv NCC_ID "$NCC_ID"
fi
databricks account workspaces update "$WS_ID" --network-connectivity-config-id "$NCC_ID" --profile "$ACCT" -o json >/dev/null
echo "NCC $NCC_ID attached to workspace $WS_ID"

RULE_ID=$(get PE_RULE_ID)
if [ -z "$RULE_ID" ]; then
  RULE_ID=$(databricks account network-connectivity create-private-endpoint-rule "$NCC_ID" --profile "$ACCT" -o json \
    --json "{\"resource_id\": \"$WEBAPP_ID\", \"group_id\": \"sites\"}" | python3 -c "import json,sys; print(json.load(sys.stdin)['rule_id'])")
  setenv PE_RULE_ID "$RULE_ID"
fi

public_access() { az resource show --ids "$WEBAPP_ID" --query properties.publicNetworkAccess -o tsv 2>/dev/null || echo unknown; }
fail() { echo "$1"; echo "publicNetworkAccess = $(public_access)"; echo "rollback: bash infra/41_public_access.sh enable"; exit 1; }
rule_json() { databricks account network-connectivity get-private-endpoint-rule "$NCC_ID" "$RULE_ID" --profile "$ACCT" -o json; }
STATE=""
for i in $(seq 1 60); do
  STATE=$(rule_json | python3 -c "import json,sys; print(json.load(sys.stdin).get('connection_state',''))")
  echo "$(date -u +%H:%M:%SZ) rule $RULE_ID state=$STATE"
  case "$STATE" in
    ESTABLISHED) break ;;
    REJECTED|CREATE_FAILED|DISCONNECTED|EXPIRED)
      rule_json | python3 -c "import json,sys; d=json.load(sys.stdin); print('error_message:', d.get('error_message'))"; fail "rule reached terminal state $STATE" ;;
  esac
  PENDING_CONN=$(az network private-endpoint-connection list --id "$WEBAPP_ID" \
    --query "[?properties.privateLinkServiceConnectionState.status=='Pending'].id | [0]" -o tsv 2>/dev/null || true)
  if [ -n "$PENDING_CONN" ]; then
    az network private-endpoint-connection approve --id "$PENDING_CONN" --description "Lab: Databricks serverless NCC" -o none
    echo "approved Azure connection $PENDING_CONN"
  fi
  sleep 30
done
[ "$STATE" = "ESTABLISHED" ] || fail "timed out (30 min) waiting for ESTABLISHED"
echo "P1 OK: private endpoint rule $RULE_ID ESTABLISHED"
echo "publicNetworkAccess = $(public_access)  (Disabled is expected after approval; rollback: bash infra/41_public_access.sh enable)"
