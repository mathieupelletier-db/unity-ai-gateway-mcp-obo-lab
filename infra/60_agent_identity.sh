#!/usr/bin/env bash
# Client App: register the backend Entra app as a Databricks service principal, assign it to the lab workspace,
# grant it the workspace-access entitlement, then prove the app identity can call the model.
# Does NOT grant anything on system.ai; model EXECUTE grants must be added separately by a metastore admin. A 403 from the final check means they are still missing.
set -euo pipefail
cd "$(dirname "$0")/.."
ACCT="<your-cli-account-profile>"
get() { grep "^$1=" .lab.env | cut -d= -f2 || true; }
BE=$(get BACKEND_CLIENT_ID); WS_ID=$(get WORKSPACE_ID)
# Fail closed before any account-level call: a blank or malformed ID must never reach list, create, or assign.
[[ "$BE" =~ ^[0-9a-fA-F-]{36}$ ]] || { echo "BACKEND_CLIENT_ID in .lab.env is missing or not a GUID" >&2; exit 1; }
[[ "$WS_ID" =~ ^[0-9]+$ ]] || { echo "WORKSPACE_ID in .lab.env is missing or not numeric" >&2; exit 1; }
SP_ID=$(databricks account service-principals list --filter "applicationId eq \"$BE\"" --profile "$ACCT" -o json \
  | python3 -c '
import json,sys
d=json.load(sys.stdin); d=d if isinstance(d,list) else d.get("Resources",[])
m=[s for s in d if (s.get("applicationId") or "").lower()==sys.argv[1].lower()]
if len(m)>1:
    sys.stderr.write("more than one service principal has applicationId %s\n" % sys.argv[1]); sys.exit(1)
print(m[0]["id"] if m else "")' "$BE")
if [ -z "$SP_ID" ]; then
  SP_ID=$(databricks account service-principals create --application-id "$BE" --display-name mcp-lab-agent-backend \
    --profile "$ACCT" -o json | python3 -c '
import json,sys
s=json.load(sys.stdin)
if (s.get("applicationId") or "").lower()!=sys.argv[1].lower():
    sys.stderr.write("create returned applicationId %s, expected %s\n" % (s.get("applicationId"), sys.argv[1])); sys.exit(1)
print(s["id"])' "$BE")
fi
[ -n "$SP_ID" ] || { echo "could not resolve a service principal id" >&2; exit 1; }
databricks account workspace-assignment update "$WS_ID" "$SP_ID" --json '{"permissions": ["USER"]}' --profile "$ACCT" -o json >/dev/null
{ grep -v '^AGENT_SP_ID=' .lab.env || true; echo "AGENT_SP_ID=$SP_ID"; } > .lab.env.tmp && mv .lab.env.tmp .lab.env
echo "SP $SP_ID (applicationId $BE) assigned to workspace $WS_ID as USER"
# Unity Gateway rejects callers without a workspace entitlement (403); account assignment alone grants none here.
databricks service-principals patch "$SP_ID" --json '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"entitlements","value":[{"value":"workspace-access"}]}]}' --profile "<your-cli-workspace-profile>" -o json >/dev/null
echo "SP $SP_ID granted workspace-access"
# Functional check (get-effective needs READ METADATA on the system model service, which this caller lacks): call the model as the app.
# Reads .lab.env/.lab.secrets in-process and prints only the status (plus a short error body on failure); never the token or the secret.
uv run python -I - <<'PY'
import sys
import time

import httpx
import msal


def load(path):
    return {k: v for k, _, v in (line.partition("=") for line in open(path).read().splitlines() if "=" in line)}


env, sec = load(".lab.env"), load(".lab.secrets")
app = msal.ConfidentialClientApplication(env["BACKEND_CLIENT_ID"], client_credential=sec["BACKEND_CLIENT_SECRET"],
                                         authority="https://login.microsoftonline.com/" + env["TENANT_ID"])
tok = app.acquire_token_for_client(scopes=["2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"])
if "access_token" not in tok:
    print("app-identity LLM call: token acquisition failed (%s)" % tok.get("error"))
    sys.exit(1)
url = "https://" + env["WORKSPACE_HOST"] + "/ai-gateway/mlflow/v1/chat/completions"
body = {"model": "system.ai.claude-haiku-5-5", "max_tokens": 20, "messages": [{"role": "user", "content": "Say OK"}]}
for attempt in range(3):  # a fresh entitlement can take a moment to propagate: retry 403 only
    try:
        r = httpx.post(url, json=body, headers={"Authorization": "Bearer " + tok["access_token"]}, timeout=60)
    except httpx.HTTPError as e:
        print("app-identity LLM call: transport error (%s)" % type(e).__name__)
        sys.exit(1)
    if r.status_code != 403 or attempt == 2:
        break
    time.sleep(20)
print("app-identity LLM call: %s" % r.status_code)
if r.status_code != 200:
    print(r.text[:200])
    sys.exit(1)
PY
