#!/usr/bin/env bash
# Resource group + serverless Azure Databricks workspace in canadacentral, assigned to a Unity Catalog metastore in the same region.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/.."

SUB="<SUBSCRIPTION_ID>"
RG=rg-mcp-obo-lab
LOC=canadacentral
WS=mcp-obo-lab
METASTORE_ID=54bee055-ef36-4397-a47f-d5b056b59dde
ACCT_PROFILE="<your-cli-account-profile>"

az account set --subscription "$SUB"
az group create -n "$RG" -l "$LOC" --tags owner="<your-alias>" purpose=mcp-obo-lab ttl="<yyyy-mm-dd>" -o none
az databricks workspace create -g "$RG" -n "$WS" -l "$LOC" --sku premium --compute-mode Serverless -o json >/dev/null

WS_HOST=$(az databricks workspace show -g "$RG" -n "$WS" --query workspaceUrl -o tsv)
WS_ID=$(az databricks workspace show -g "$RG" -n "$WS" --query workspaceId -o tsv)

if ! databricks account metastore-assignments get "$WS_ID" --profile "$ACCT_PROFILE" -o json >/dev/null 2>&1; then
  databricks account metastore-assignments create "$WS_ID" "$METASTORE_ID" --profile "$ACCT_PROFILE" -o json >/dev/null
fi
databricks account metastore-assignments get "$WS_ID" --profile "$ACCT_PROFILE" -o json

touch .lab.env
{
  grep -v -E '^(WORKSPACE_HOST|WORKSPACE_ID|DATABRICKS_USERNAME)=' .lab.env || true
  echo "WORKSPACE_HOST=$WS_HOST"
  echo "WORKSPACE_ID=$WS_ID"
  echo "DATABRICKS_USERNAME=user@example.com"
} > .lab.env.tmp
mv .lab.env.tmp .lab.env
echo "Workspace: https://$WS_HOST (id $WS_ID)"
