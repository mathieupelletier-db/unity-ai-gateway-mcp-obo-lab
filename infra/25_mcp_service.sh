#!/usr/bin/env bash
# UC HTTP connection (OAuth U2M per-user, Entra) + MCP Service with tool selection and rate limits.
# Bodies come from infra/lab_payloads.py over stdin, so the client secret never appears on argv.
# The guardrail policy (block_sensitive_data, SSN, input+output, deny) is still created in the UI (Policies tab).
set -euo pipefail
cd "$(dirname "$0")/.."
P="<your-cli-workspace-profile>"

if databricks connections get mcp_obo_lab.mcp.mcp_lab_mcp_connection --profile "$P" -o json >/dev/null 2>&1; then
  echo "connection exists; skipping create"
else
  uv run python -m infra.lab_payloads connection | databricks connections create --json @/dev/stdin --profile "$P" -o json >/dev/null
fi

if databricks ai-gateway get-mcp-service mcp-services/mcp_obo_lab.mcp.mcp_lab_mcp --profile "$P" -o json >/dev/null 2>&1; then
  echo "MCP service exists; skipping create"
else
  uv run python -m infra.lab_payloads mcp-service \
    | databricks ai-gateway create-mcp-service schemas/mcp_obo_lab.mcp mcp_lab_mcp --json @/dev/stdin --profile "$P" -o json >/dev/null
fi

echo "Next: each user signs in once at https://$(grep ^WORKSPACE_HOST= .lab.env | cut -d= -f2)/mcp-service-login?name=mcp_obo_lab.mcp.mcp_lab_mcp"
