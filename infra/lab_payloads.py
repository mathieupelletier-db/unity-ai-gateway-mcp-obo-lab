"""Request bodies for the lab's UC connection and MCP Service, in the configuration that worked with Entra.

Prints JSON to stdout so callers pipe it to `databricks ... --json @/dev/stdin`; the client secret never goes on argv.
Usage: uv run python -m infra.lab_payloads {connection|mcp-service|atlassian-connection|atlassian-mcp-service [tools]}
"""
import json
import sys
from urllib.parse import urlsplit

from client.config import ROOT, read_env_file

CATALOG, SCHEMA = "mcp_obo_lab", "mcp"
CONNECTION, SERVICE = "mcp_lab_mcp_connection", "mcp_lab_mcp"
ATLASSIAN_CONNECTION, ATLASSIAN_SERVICE = "mcp_lab_atlassian_conn", "mcp_lab_atlassian"
ATLASSIAN_RESOURCE = "https://mcp.atlassian.com/v2/mcp"  # RFC 8707 resource; the connection host is the App Gateway
ATLASSIAN_AUTHORIZE = "https://auth.atlassian.com/authorize"
ATLASSIAN_TOKEN = "https://auth.atlassian.com/oauth/token"
ATLASSIAN_REGISTER = "https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3/dcr/register"
ATLASSIAN_SCOPES = ("read:me read:account offline_access read:jira:agent-interface search:jira:agent-interface "
                    "read:confluence:agent-interface search:confluence:agent-interface search:rovo:agent-interface")
APP_ID_URI_HOST = "<your-tenant>.onmicrosoft.com"  # *.onmicrosoft.com is always a verified tenant domain


def app_id_uri() -> str:
    """https App ID URI on the MCP API app. Sent as RFC 8707 `resource`; Entra v2 requires it to match the scope."""
    return f"https://{APP_ID_URI_HOST}/mcp-lab-mcp-api"


def connection_payload(env: dict, secrets: dict) -> dict:
    # The connection's OAuth client must be its own app: when the MCP API app requests a token for itself with an
    # https resource, Entra accepts the code grant but rejects every refresh (AADSTS90009) about an hour later.
    if env["UC_CONN_CLIENT_ID"] == env["MCP_API_CLIENT_ID"]:
        raise ValueError("UC connection OAuth client must be a separate app registration from the MCP API app (AADSTS90009)")
    url = urlsplit(env["MCP_SERVER_URL"])
    login = f"https://login.microsoftonline.com/{env['TENANT_ID']}/oauth2/v2.0"
    return {
        "name": CONNECTION,
        "parent": f"schemas/{CATALOG}.{SCHEMA}",
        "connection_type": "HTTP",
        "credential_type": "OAUTH_U2M_MAPPING",  # as reported by GET on the working connection
        "comment": "Lab: Entra-protected MCP via U2M per-user (synthetic)",
        "options": {
            "host": f"{url.scheme}://{url.netloc}",
            "base_path": url.path,
            "client_id": env["UC_CONN_CLIENT_ID"],
            "client_secret": secrets["UC_CONN_CLIENT_SECRET"],
            "authorization_endpoint": f"{login}/authorize",
            "token_endpoint": f"{login}/token",
            "oauth_scope": f"{app_id_uri()}/mcp.invoke offline_access",
            "mcp_resource": app_id_uri(),
            "oauth_credential_exchange_method": "body_only",
        },
    }


def _rate_limits() -> list[dict]:
    minute = "RATE_LIMIT_RENEWAL_PERIOD_MINUTE"
    return [
        {"key": "RATE_LIMIT_KEY_SERVICE", "renewal_period": minute, "requests": 60},
        {"key": "RATE_LIMIT_KEY_USER_DEFAULT", "renewal_period": minute, "requests": 10},
    ]


def mcp_service_payload() -> dict:
    return {
        "comment": "Lab: Entra-protected MCP via U2M per-user (synthetic)",
        "config": {
            "source_connection": {"name": f"connections/{CATALOG}.{SCHEMA}.{CONNECTION}"},
            "include_tool_selectors": ["whoami", "echo"],
            "rate_limits": _rate_limits(),
        },
    }


def atlassian_connection_payload(env: dict, secrets: dict) -> dict:
    host = env["AGW_FQDN"]
    if host.endswith("atlassian.com"):
        raise ValueError("Atlassian connection host must be the App Gateway, not Atlassian directly")
    return {
        "name": ATLASSIAN_CONNECTION,
        "parent": f"schemas/{CATALOG}.{SCHEMA}",
        "connection_type": "HTTP",
        "credential_type": "OAUTH_U2M_MAPPING",
        "comment": "Lab: Atlassian Rovo MCP via the App Gateway, U2M per-user (synthetic)",
        "options": {
            "host": f"https://{host}",
            "base_path": "/v2/mcp",
            "client_id": env["ATLASSIAN_CLIENT_ID"],
            "client_secret": secrets["ATLASSIAN_CLIENT_SECRET"],
            "authorization_endpoint": ATLASSIAN_AUTHORIZE,
            "token_endpoint": ATLASSIAN_TOKEN,
            "oauth_scope": ATLASSIAN_SCOPES,
            "mcp_resource": ATLASSIAN_RESOURCE,
            "oauth_credential_exchange_method": "body_only",
        },
    }


def atlassian_mcp_service_payload(tool_names: list[str] | None = None) -> dict:
    config = {"source_connection": {"name": f"connections/{CATALOG}.{SCHEMA}.{ATLASSIAN_CONNECTION}"}, "rate_limits": _rate_limits()}
    if tool_names:
        config["include_tool_selectors"] = list(tool_names)
    return {"comment": "Lab: Atlassian Rovo MCP via the App Gateway (synthetic)", "config": config}


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    if kind == "connection":
        body = connection_payload(read_env_file(ROOT / ".lab.env"), read_env_file(ROOT / ".lab.secrets"))
    elif kind == "mcp-service":
        body = mcp_service_payload()
    elif kind == "atlassian-connection":
        body = atlassian_connection_payload(read_env_file(ROOT / ".lab.env"), read_env_file(ROOT / ".lab.secrets"))
    elif kind == "atlassian-mcp-service":
        tools = [t for t in (sys.argv[2] if len(sys.argv) > 2 else "").split(",") if t]
        body = atlassian_mcp_service_payload(tools or None)
    else:
        raise SystemExit("usage: python -m infra.lab_payloads {connection|mcp-service|atlassian-connection|atlassian-mcp-service [tools]}")
    json.dump(body, sys.stdout)


if __name__ == "__main__":
    main()
