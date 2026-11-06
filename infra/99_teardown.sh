#!/usr/bin/env bash
# Remove every lab resource. Run only when the lab is no longer needed.
# Local secret files (.lab.secrets, .lab.msal-cache.json) are left for you to delete by hand.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/.."
get() { grep "^$1=" .lab.env | cut -d= -f2 || true; }

databricks ai-gateway delete-mcp-service mcp-services/mcp_obo_lab.mcp.mcp_lab_mcp --profile "<your-cli-workspace-profile>" || true
databricks ai-gateway delete-mcp-service mcp-services/mcp_obo_lab.mcp.mcp_lab_mcp_agw --profile "<your-cli-workspace-profile>" || true  # spike
databricks connections delete mcp_obo_lab.mcp.mcp_lab_mcp_agw_connection --profile "<your-cli-workspace-profile>" || true          # spike
databricks ai-gateway delete-mcp-service mcp-services/mcp_obo_lab.mcp.mcp_lab_atlassian --profile "<your-cli-workspace-profile>" || true
databricks connections delete mcp_obo_lab.mcp.mcp_lab_atlassian_conn --profile "<your-cli-workspace-profile>" || true
databricks connections delete mcp_obo_lab.mcp.mcp_lab_mcp_connection --profile "<your-cli-workspace-profile>" || true
databricks schemas delete mcp_obo_lab.mcp --force --profile "<your-cli-workspace-profile>" || true
databricks catalogs delete mcp_obo_lab --force --profile "<your-cli-workspace-profile>" || true
FED=$(get FEDERATION_POLICY_ID)
if [ -n "$FED" ]; then databricks account federation-policy delete "$FED" --profile "<your-cli-account-profile>" || true; fi
for id in "$(get FRONTEND_CLIENT_ID)" "$(get BACKEND_CLIENT_ID)" "$(get MCP_API_CLIENT_ID)" "$(get UC_CONN_CLIENT_ID)"; do
  [ -n "$id" ] && { az ad app delete --id "$id" || true; }
done
# Workspace (incl. SQL warehouse mcp-lab-wh) + App Service plan/app go with the resource group.
NCC=$(get NCC_ID); RULE=$(get PE_RULE_ID)
if [ -n "$NCC" ] && [ -n "$RULE" ]; then
  databricks account network-connectivity delete-private-endpoint-rule "$NCC" "$RULE" --profile "<your-cli-account-profile>" || true
fi
SP=$(get AGENT_SP_ID)
if [ -n "$SP" ]; then databricks account service-principals delete "$SP" --profile "<your-cli-account-profile>" || true; fi
az group delete -n rg-mcp-obo-lab --yes --no-wait
if [ -n "$NCC" ]; then
  databricks account network-connectivity delete-network-connectivity-configuration "$NCC" --profile "<your-cli-account-profile>" \
    || echo "NCC $NCC still attached (workspace deletion is async). Re-run later: databricks account network-connectivity delete-network-connectivity-configuration $NCC --profile <your-cli-account-profile>"
fi
echo "Teardown submitted. RG deletion (workspace, App Service, spike App Gateway/VNet/PE/DNS/storage) runs in the background."
echo "Delete by hand (secrets and private keys): .lab.secrets .lab.msal-cache.json build/agw build/certbot"
echo "Atlassian (by hand): revoke the lab app grant at id.atlassian.com → Connected apps; the DCR client itself cannot be deleted by this script (no RFC 7592 management token was stored) — remove ATLASSIAN_CLIENT_SECRET with the other secrets so it is unusable; delete Jira project LAB (and LAB-1) on <your-site>.atlassian.net."
echo "Also removed with the RG: Log Analytics workspace law-mcp-lab (App Gateway access logs)."
