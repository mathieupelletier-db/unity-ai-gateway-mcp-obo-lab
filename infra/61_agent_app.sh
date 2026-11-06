#!/usr/bin/env bash
# Client App: snet-app (+NAT) + App Service (existing B1 plan) with VNet integration (route-all), deploy, settings, SPA redirect.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/.."
umask 077
# The backend client secret is written to this file for `appsettings set`; never leave it on disk, on success or failure.
trap '[ -f build/agent-appsettings.json ] && : > build/agent-appsettings.json' EXIT
RG=rg-mcp-obo-lab; LOC=canadacentral; VNET=vnet-mcp-lab; PLAN=asp-mcp-lab
get() { grep "^$1=" .lab.env | cut -d= -f2 || true; }
setenv() { { grep -v "^$1=" .lab.env || true; echo "$1=$2"; } > .lab.env.tmp && mv .lab.env.tmp .lab.env; }
APP=$(get AGENT_APP_NAME); APP=${APP:-mcp-lab-agent-$(python3 -c "import secrets; print(secrets.token_hex(2))")}; setenv AGENT_APP_NAME "$APP"

az network public-ip show -g "$RG" -n pip-mcp-lab-nat -o none 2>/dev/null || az network public-ip create -g "$RG" -n pip-mcp-lab-nat -l "$LOC" --sku Standard --allocation-method Static -o none
az network nat gateway show -g "$RG" -n nat-mcp-lab -o none 2>/dev/null || az network nat gateway create -g "$RG" -n nat-mcp-lab -l "$LOC" --public-ip-addresses pip-mcp-lab-nat -o none
az network vnet subnet show -g "$RG" --vnet-name "$VNET" -n snet-app -o none 2>/dev/null || \
  az network vnet subnet create -g "$RG" --vnet-name "$VNET" -n snet-app --address-prefixes 10.42.3.0/24 \
    --delegations Microsoft.Web/serverFarms --nat-gateway nat-mcp-lab -o none

az webapp show -g "$RG" -n "$APP" -o none 2>/dev/null || az webapp create -g "$RG" -p "$PLAN" -n "$APP" --runtime "PYTHON:3.12" -o none
az webapp vnet-integration list -g "$RG" -n "$APP" --query "[0].name" -o tsv | grep -q . || \
  az webapp vnet-integration add -g "$RG" -n "$APP" --vnet "$VNET" --subnet snet-app -o none
az webapp config set -g "$RG" -n "$APP" --vnet-route-all-enabled true \
  --startup-file "python -m uvicorn agent_app.asgi:app --host 0.0.0.0 --port 8000" -o none

python3 - <<'EOF'
import json, os, sys
sys.path.insert(0, ".")
from infra.lab_payloads import app_id_uri
env ={k: v for k, _, v in (l.partition("=") for l in open(".lab.env").read().splitlines() if "=" in l)}
sec = {k: v for k, _, v in (l.partition("=") for l in open(".lab.secrets").read().splitlines() if "=" in l)}
settings = [{"name": n, "value": v, "slotSetting": False} for n, v in {
    "ENTRA_TENANT_ID": env["TENANT_ID"], "BACKEND_CLIENT_ID": env["BACKEND_CLIENT_ID"], "BACKEND_CLIENT_SECRET": sec["BACKEND_CLIENT_SECRET"],
    "FRONTEND_CLIENT_ID": env["FRONTEND_CLIENT_ID"], "WORKSPACE_HOST": env["WORKSPACE_HOST"],
    "MCP_SERVICE_NAME": env["MCP_SERVICE_NAME"], "MCP_SERVICE_NAMES": ",".join([env["MCP_SERVICE_NAME"], "mcp_obo_lab.mcp.mcp_lab_atlassian"]), "LLM_MODEL": "system.ai.claude-haiku-5-5", "SCM_DO_BUILD_DURING_DEPLOYMENT": "true",
    # App-driven sign-in: Entra silently (prompt=none). Atlassian keeps the gateway popup: our accounts are managed by an Atlassian org whose
    # MCP domain allowlist blocks this app's domain. Redirect entry for an org that allowlists the app (the production path):
    #   {"service": "mcp_obo_lab.mcp.mcp_lab_atlassian", "client_id": env["ATLASSIAN_CLIENT_ID"], "scope": ATLASSIAN_SCOPES,
    #    "authorize_url": ATLASSIAN_AUTHORIZE, "prompt": "consent", "resource": ATLASSIAN_RESOURCE}
    "TOOL_SIGNIN_JSON": json.dumps([
        {"service": env["MCP_SERVICE_NAME"], "client_id": env["UC_CONN_CLIENT_ID"], "scope": f"{app_id_uri()}/mcp.invoke offline_access",
         "authorize_url": f"https://login.microsoftonline.com/{env['TENANT_ID']}/oauth2/v2.0/authorize"}])}.items()]
os.makedirs("build", exist_ok=True)
fd = os.open("build/agent-appsettings.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(settings, f)
EOF
az webapp config appsettings set -g "$RG" -n "$APP" --settings @build/agent-appsettings.json -o none

URL="https://$APP.azurewebsites.net"; setenv AGENT_APP_URL "$URL"
# SPA redirect: union with the frontend app's existing SPA redirect URIs. Never overwrite them.
FE_OBJ=$(az ad app show --id "$(get FRONTEND_CLIENT_ID)" --query id -o tsv)
SPA_EXISTING=$(az ad app show --id "$(get FRONTEND_CLIENT_ID)" --query "spa.redirectUris" -o json)
SPA_BODY=$(python3 -I -c '
import json, sys
existing = json.loads(sys.stdin.read()) or []
uris = []
for u in existing + [sys.argv[1]]:
    if u not in uris:
        uris.append(u)
print(json.dumps({"spa": {"redirectUris": uris}}))' "$URL/" <<<"$SPA_EXISTING")
az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$FE_OBJ" --headers Content-Type=application/json --body "$SPA_BODY" -o none
# Silent tool sign-in: the UC connection's OAuth client (web platform) must also accept the app's /tools-callback. Union, never overwrite.
UC_OBJ=$(az ad app show --id "$(get UC_CONN_CLIENT_ID)" --query id -o tsv)
WEB_EXISTING=$(az ad app show --id "$(get UC_CONN_CLIENT_ID)" --query "web.redirectUris" -o json)
WEB_BODY=$(python3 -I -c '
import json, sys
uris = []
for u in (json.loads(sys.stdin.read()) or []) + [sys.argv[1]]:
    if u not in uris:
        uris.append(u)
print(json.dumps({"web": {"redirectUris": uris}}))' "$URL/tools-callback" <<<"$WEB_EXISTING")
az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$UC_OBJ" --headers Content-Type=application/json --body "$WEB_BODY" -o none

STAGE="build/agent-$(date +%Y%m%d%H%M%S)"; mkdir -p "$STAGE/mcp_server"
rsync -a --exclude __pycache__ agent_app client "$STAGE/"
basename "$STAGE" > "$STAGE/agent_app/BUILD"   # build marker served at /api/version; the deploy is verified against it
cp mcp_server/__init__.py mcp_server/auth.py "$STAGE/mcp_server/"
uv export --no-dev --no-hashes --no-emit-project --format requirements-txt > "$STAGE/requirements.txt"
(cd "$STAGE" && zip -qr "../$(basename "$STAGE").zip" .)   # one zip per stage; zip -r into an existing archive keeps stale entries
# config-zip (Kudu zipdeploy), not `az webapp deploy`: OneDeploy skipped the Oryx build here, so requirements were never installed.
# The deploy call can report failure on a stale LastError even when the build runs, so success is decided by the running build marker below.
az webapp deployment source config-zip -g "$RG" -n "$APP" --src "$STAGE.zip" -o none || echo "deploy status reported failure; verifying the running build" >&2

MARKER=$(basename "$STAGE"); POLL_TRIES=${POLL_TRIES:-45}; POLL_SLEEP=${POLL_SLEEP:-20}   # 45 x 20 s = 15 min
for ((i = 1; i <= POLL_TRIES; i++)); do
  RUNNING=$(curl -s --max-time 30 "$URL/api/version" | python3 -I -c '
import json, sys
try:
    print(json.load(sys.stdin).get("build", ""))
except Exception:
    print("")' || true)
  if [ "$RUNNING" = "$MARKER" ]; then
    echo "Client App: $URL/ (build $MARKER)"
    exit 0
  fi
  [ "$i" -lt "$POLL_TRIES" ] && sleep "$POLL_SLEEP"
done
echo "ERROR: $URL/api/version did not report build $MARKER within $((POLL_TRIES * POLL_SLEEP)) s" >&2
exit 1

