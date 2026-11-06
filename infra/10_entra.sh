#!/usr/bin/env bash
# Three lab app registrations in your Entra tenant. Secrets go to .lab.secrets only.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/.."
umask 077

TENANT_ID="<TENANT_ID>"
AZDBX_APP=2ff814a6-3304-4ab8-85cb-cd0e6f879c1d
WS_HOST=$(grep ^WORKSPACE_HOST= .lab.env | cut -d= -f2)
END_DATE=$(date -v+30d +%Y-%m-%d)
uuid() { python3 -c "import uuid; print(uuid.uuid4())"; }
graph_patch() { az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$1" --headers Content-Type=application/json --body "$2" -o none; }
scope_json() { # id value description
  printf '{"id":"%s","value":"%s","type":"User","isEnabled":true,"adminConsentDisplayName":"%s","adminConsentDescription":"%s","userConsentDisplayName":"%s","userConsentDescription":"%s"}' "$1" "$2" "$3" "$3" "$3" "$3"
}
OPTIONAL_EMAIL='"optionalClaims":{"accessToken":[{"name":"email","essential":false}]}'

# 1. MCP API (resource + UC connection OAuth client)
# Redirects: sign-in from the MCP create form returns to the account's unified login host, sign-in from the
# connection page returns to the workspace host; both must be registered.
ACCOUNT_LOGIN_HOST="<unified-login-host>"
MCP_APP_ID=$(az ad app create --display-name mcp-lab-mcp-api --sign-in-audience AzureADMyOrg \
  --web-redirect-uris "https://$WS_HOST/login/oauth/http.html" "https://$ACCOUNT_LOGIN_HOST/login/oauth/http.html" \
  --query appId -o tsv)
MCP_OBJ=$(az ad app show --id "$MCP_APP_ID" --query id -o tsv)
MCP_SCOPE_ID=$(uuid)
# The https App ID URI is what the UC connection sends as RFC 8707 `resource` (mcp_resource rejects api://);
# Entra v2 fails with AADSTS9010010 unless resource and scope name the same resource. See infra/lab_payloads.py.
az ad app update --id "$MCP_APP_ID" --identifier-uris "api://$MCP_APP_ID" "https://<your-tenant>.onmicrosoft.com/mcp-lab-mcp-api"
graph_patch "$MCP_OBJ" "{\"api\":{\"requestedAccessTokenVersion\":2,\"oauth2PermissionScopes\":[$(scope_json "$MCP_SCOPE_ID" mcp.invoke "Invoke MCP lab MCP tools as the signed-in user")]},$OPTIONAL_EMAIL}"
graph_patch "$MCP_OBJ" "{\"requiredResourceAccess\":[{\"resourceAppId\":\"$MCP_APP_ID\",\"resourceAccess\":[{\"id\":\"$MCP_SCOPE_ID\",\"type\":\"Scope\"}]}]}"
az ad sp create --id "$MCP_APP_ID" -o none
MCP_SECRET=$(az ad app credential reset --id "$MCP_APP_ID" --display-name uc-connection --end-date "$END_DATE" --query password -o tsv)

# 2. Agent backend (API + confidential client doing OBO)
BE_APP_ID=$(az ad app create --display-name mcp-lab-agent-backend --sign-in-audience AzureADMyOrg --query appId -o tsv)
BE_OBJ=$(az ad app show --id "$BE_APP_ID" --query id -o tsv)
BE_SCOPE_ID=$(uuid)
az ad app update --id "$BE_APP_ID" --identifier-uris "api://$BE_APP_ID"
BE_SCOPES="[$(scope_json "$BE_SCOPE_ID" access_as_user "Call the MCP lab agent backend as the signed-in user")]"
graph_patch "$BE_OBJ" "{\"api\":{\"requestedAccessTokenVersion\":2,\"oauth2PermissionScopes\":$BE_SCOPES},$OPTIONAL_EMAIL}"
AZDBX_SCOPE_ID=$(az ad sp show --id "$AZDBX_APP" --query "oauth2PermissionScopes[?value=='user_impersonation'].id | [0]" -o tsv)
graph_patch "$BE_OBJ" "{\"requiredResourceAccess\":[{\"resourceAppId\":\"$AZDBX_APP\",\"resourceAccess\":[{\"id\":\"$AZDBX_SCOPE_ID\",\"type\":\"Scope\"}]},{\"resourceAppId\":\"$MCP_APP_ID\",\"resourceAccess\":[{\"id\":\"$MCP_SCOPE_ID\",\"type\":\"Scope\"}]}]}"
az ad sp create --id "$BE_APP_ID" -o none
BE_SECRET=$(az ad app credential reset --id "$BE_APP_ID" --display-name obo --end-date "$END_DATE" --query password -o tsv)

# 3. Front-end (public client) + combined consent through knownClientApplications
FE_APP_ID=$(az ad app create --display-name mcp-lab-frontend --sign-in-audience AzureADMyOrg \
  --public-client-redirect-uris "http://localhost" --is-fallback-public-client true --query appId -o tsv)
FE_OBJ=$(az ad app show --id "$FE_APP_ID" --query id -o tsv)
graph_patch "$FE_OBJ" "{\"requiredResourceAccess\":[{\"resourceAppId\":\"$BE_APP_ID\",\"resourceAccess\":[{\"id\":\"$BE_SCOPE_ID\",\"type\":\"Scope\"}]}]}"
az ad sp create --id "$FE_APP_ID" -o none
graph_patch "$BE_OBJ" "{\"api\":{\"requestedAccessTokenVersion\":2,\"knownClientApplications\":[\"$FE_APP_ID\"],\"oauth2PermissionScopes\":$BE_SCOPES}}"

touch .lab.env
{
  grep -v -E '^(TENANT_ID|FRONTEND_CLIENT_ID|BACKEND_CLIENT_ID|MCP_API_CLIENT_ID)=' .lab.env || true
  echo "TENANT_ID=$TENANT_ID"
  echo "FRONTEND_CLIENT_ID=$FE_APP_ID"
  echo "BACKEND_CLIENT_ID=$BE_APP_ID"
  echo "MCP_API_CLIENT_ID=$MCP_APP_ID"
} > .lab.env.tmp
mv .lab.env.tmp .lab.env
printf 'BACKEND_CLIENT_SECRET=%s\nMCP_API_CLIENT_SECRET=%s\n' "$BE_SECRET" "$MCP_SECRET" > .lab.secrets
chmod 600 .lab.secrets
echo "Created apps: frontend=$FE_APP_ID backend=$BE_APP_ID mcp-api=$MCP_APP_ID (secrets written to .lab.secrets)"

# Separate OAuth client app for the UC connection (AADSTS90009 otherwise)
bash infra/15_uc_conn_app.sh  # cwd is the lab root (cd at top of this script)
