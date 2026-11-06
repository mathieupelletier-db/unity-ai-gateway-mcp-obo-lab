"""Load lab settings from .lab.env (non-secret) and .lab.secrets (secret, mode 600)."""
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = (
    "TENANT_ID", "FRONTEND_CLIENT_ID", "BACKEND_CLIENT_ID", "BACKEND_CLIENT_SECRET", "MCP_API_CLIENT_ID",
    "WORKSPACE_HOST", "MCP_SERVICE_NAME", "MCP_SERVER_URL", "DATABRICKS_USERNAME",
)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')
    return values


@dataclass(frozen=True)
class LabConfig:
    tenant_id: str
    frontend_client_id: str
    backend_client_id: str
    backend_client_secret: str
    mcp_api_client_id: str
    workspace_host: str
    mcp_service_name: str  # <catalog>.<schema>.<service>
    mcp_server_url: str    # direct App Service URL ending in /mcp
    databricks_username: str

    @property
    def gateway_url(self) -> str:
        return f"https://{self.workspace_host}/ai-gateway/mcp-services/{self.mcp_service_name}"

    @classmethod
    def load(cls, root: Path = ROOT) -> "LabConfig":
        v = {**read_env_file(root / ".lab.env"), **read_env_file(root / ".lab.secrets")}
        missing = [k for k in KEYS if not v.get(k)]
        if missing:
            raise RuntimeError(f"missing lab settings: {', '.join(missing)}")
        return cls(
            tenant_id=v["TENANT_ID"], frontend_client_id=v["FRONTEND_CLIENT_ID"],
            backend_client_id=v["BACKEND_CLIENT_ID"], backend_client_secret=v["BACKEND_CLIENT_SECRET"],
            mcp_api_client_id=v["MCP_API_CLIENT_ID"], workspace_host=v["WORKSPACE_HOST"],
            mcp_service_name=v["MCP_SERVICE_NAME"], mcp_server_url=v["MCP_SERVER_URL"],
            databricks_username=v["DATABRICKS_USERNAME"],
        )
