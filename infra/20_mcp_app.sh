#!/usr/bin/env bash
# Package mcp_server/ with a requirements.txt and deploy it to a Linux Python App Service in canadacentral.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/.."

RG=rg-mcp-obo-lab
LOC=canadacentral
PLAN=asp-mcp-lab
APP=$(grep ^MCP_APP_NAME= .lab.env | cut -d= -f2 || true)
APP=${APP:-mcp-lab-mcp-$(python3 -c "import secrets; print(secrets.token_hex(2))")}
TENANT_ID=$(grep ^TENANT_ID= .lab.env | cut -d= -f2)
MCP_API_CLIENT_ID=$(grep ^MCP_API_CLIENT_ID= .lab.env | cut -d= -f2)

# Fresh staging dir per run (no removals needed)
STAGE="build/app-$(date +%Y%m%d%H%M%S)"
mkdir -p "$STAGE"
rsync -a --exclude __pycache__ mcp_server "$STAGE/"
uv export --no-dev --no-hashes --no-emit-project --format requirements-txt > "$STAGE/requirements.txt"

(cd "$STAGE" && az webapp up -g "$RG" -n "$APP" -p "$PLAN" -l "$LOC" --sku B1 --runtime "PYTHON:3.12" -o none)
az webapp config appsettings set -g "$RG" -n "$APP" -o none --settings \
  ENTRA_TENANT_ID="$TENANT_ID" MCP_API_CLIENT_ID="$MCP_API_CLIENT_ID" SCM_DO_BUILD_DURING_DEPLOYMENT=true
az webapp config set -g "$RG" -n "$APP" -o none \
  --startup-file "python -m uvicorn mcp_server.asgi:app --host 0.0.0.0 --port 8000"
az webapp restart -g "$RG" -n "$APP"

{
  grep -v -E '^(MCP_APP_NAME|MCP_SERVER_URL)=' .lab.env || true
  echo "MCP_APP_NAME=$APP"
  echo "MCP_SERVER_URL=https://$APP.azurewebsites.net/mcp"
} > .lab.env.tmp
mv .lab.env.tmp .lab.env
echo "MCP server: https://$APP.azurewebsites.net/mcp (staged from $STAGE)"
