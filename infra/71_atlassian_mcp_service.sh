#!/usr/bin/env bash
# Atlassian leg: UC HTTP connection (OAuth U2M per user; host = App Gateway; mcp_resource = Atlassian) + MCP Service.
# Optional $1 = comma-separated tool names to set as include_tool_selectors on the existing service.
set -euo pipefail
cd "$(dirname "$0")/.."
P="<your-cli-workspace-profile>"; CONN=mcp_obo_lab.mcp.mcp_lab_atlassian_conn; SVC=mcp-services/mcp_obo_lab.mcp.mcp_lab_atlassian

if databricks connections get "$CONN" --profile "$P" -o json >/dev/null 2>&1; then
  echo "connection exists; skipping create"
else
  uv run python -m infra.lab_payloads atlassian-connection | databricks connections create --json @/dev/stdin --profile "$P" -o json >/dev/null
fi
if databricks ai-gateway get-mcp-service "$SVC" --profile "$P" -o json >/dev/null 2>&1; then
  echo "MCP service exists; skipping create"
else
  uv run python -m infra.lab_payloads atlassian-mcp-service \
    | databricks ai-gateway create-mcp-service schemas/mcp_obo_lab.mcp mcp_lab_atlassian --json @/dev/stdin --profile "$P" -o json >/dev/null
fi
if [ -n "${1:-}" ]; then
  uv run python -m infra.lab_payloads atlassian-mcp-service "$1" \
    | databricks ai-gateway update-mcp-service "$SVC" config.include_tool_selectors --json @/dev/stdin --profile "$P" -o json >/dev/null
  echo "tool selectors set: $1"
fi
databricks connections get "$CONN" --profile "$P" -o json \
  | python3 -c "import json,sys; o=json.load(sys.stdin)['options']; print('host:', o.get('host'), '| base_path:', o.get('base_path'), '| mcp_resource:', o.get('mcp_resource'))"
echo "Sign in once at https://$(grep ^WORKSPACE_HOST= .lab.env | cut -d= -f2)/mcp-service-login?name=mcp_obo_lab.mcp.mcp_lab_atlassian"
