#!/usr/bin/env bash
# Separate Entra app for the UC connection's OAuth client (must NOT be the MCP API app: refresh fails with AADSTS90009).
# Idempotent: skips if UC_CONN_CLIENT_ID is already in .lab.env. Secret goes to .lab.secrets only.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/.."
umask 077
get() { grep "^$1=" .lab.env | cut -d= -f2 || true; }
if [ -n "$(get UC_CONN_CLIENT_ID)" ]; then echo "UC_CONN_CLIENT_ID already set; skipping"; exit 0; fi

WS_HOST=$(get WORKSPACE_HOST)
MCP_APP_ID=$(get MCP_API_CLIENT_ID)
ACCOUNT_LOGIN_HOST="<unified-login-host>"
MCP_SCOPE_ID=$(az ad app show --id "$MCP_APP_ID" --query "api.oauth2PermissionScopes[?value=='mcp.invoke'].id | [0]" -o tsv)

UC_APP_ID=$(az ad app create --display-name mcp-lab-uc-conn --sign-in-audience AzureADMyOrg \
  --web-redirect-uris "https://$WS_HOST/login/oauth/http.html" "https://$ACCOUNT_LOGIN_HOST/login/oauth/http.html" \
  --query appId -o tsv)
UC_OBJ=$(az ad app show --id "$UC_APP_ID" --query id -o tsv)
az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$UC_OBJ" --headers Content-Type=application/json \
  --body "{\"requiredResourceAccess\":[{\"resourceAppId\":\"$MCP_APP_ID\",\"resourceAccess\":[{\"id\":\"$MCP_SCOPE_ID\",\"type\":\"Scope\"}]}]}" -o none
az ad sp create --id "$UC_APP_ID" -o none
UC_SECRET=$(az ad app credential reset --id "$UC_APP_ID" --display-name uc-connection --end-date "$(date -v+30d +%Y-%m-%d)" --query password -o tsv)

{ grep -v '^UC_CONN_CLIENT_ID=' .lab.env || true; echo "UC_CONN_CLIENT_ID=$UC_APP_ID"; } > .lab.env.tmp && mv .lab.env.tmp .lab.env
{ grep -v '^UC_CONN_CLIENT_SECRET=' .lab.secrets || true; printf 'UC_CONN_CLIENT_SECRET=%s\n' "$UC_SECRET"; } > .lab.secrets.tmp && mv .lab.secrets.tmp .lab.secrets
chmod 600 .lab.secrets
echo "Created UC connection client app $UC_APP_ID (secret written to .lab.secrets)"
