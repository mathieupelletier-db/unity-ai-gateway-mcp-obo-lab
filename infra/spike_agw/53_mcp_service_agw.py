"""SPIKE (throwaway): second UC connection + MCP Service whose host is the public App Gateway.

Reuses the phase-1 payload builder (same Entra apps; separate connection client); JSON goes to the CLI over stdin.
Usage: uv run python infra/spike_agw/53_mcp_service_agw.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from client.config import read_env_file  # noqa: E402
from infra.lab_payloads import connection_payload, mcp_service_payload  # noqa: E402

P = "<your-cli-workspace-profile>"
CONN, SVC = "mcp_lab_mcp_agw_connection", "mcp_lab_mcp_agw"


def cli(*args, stdin=None):
    return subprocess.run(["databricks", *args, "--profile", P, "-o", "json"], input=stdin, capture_output=True, text=True)


env, sec = read_env_file(ROOT / ".lab.env"), read_env_file(ROOT / ".lab.secrets")
env = {**env, "MCP_SERVER_URL": f"https://{env['AGW_FQDN']}/mcp"}

if cli("connections", "get", f"mcp_obo_lab.mcp.{CONN}").returncode == 0:
    print("connection exists; skipping")
else:
    body = connection_payload(env, sec)
    body["name"] = CONN
    body["comment"] = "SPIKE: MCP via public App Gateway (lab)"
    r = cli("connections", "create", "--json", "@/dev/stdin", stdin=json.dumps(body))
    print("connection create exit", r.returncode, r.stderr[:300].strip())

if cli("ai-gateway", "get-mcp-service", f"mcp-services/mcp_obo_lab.mcp.{SVC}").returncode == 0:
    print("MCP service exists; skipping")
else:
    body = mcp_service_payload()
    body["config"]["source_connection"] = {"name": f"connections/mcp_obo_lab.mcp.{CONN}"}
    body["comment"] = "SPIKE: MCP via public App Gateway (lab)"
    r = cli("ai-gateway", "create-mcp-service", "schemas/mcp_obo_lab.mcp", SVC, "--json", "@/dev/stdin", stdin=json.dumps(body))
    print("mcp service create exit", r.returncode, r.stderr[:300].strip())

live = json.loads(cli("connections", "get", f"mcp_obo_lab.mcp.{CONN}").stdout)["options"]
print("host:", live.get("host"), "| client_id:", live.get("client_id"), "| mcp_resource:", live.get("mcp_resource"))
