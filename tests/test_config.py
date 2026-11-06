import pytest

from client.config import LabConfig, read_env_file

FULL_ENV = """# lab settings
TENANT_ID=t
FRONTEND_CLIENT_ID=fe
BACKEND_CLIENT_ID=be
MCP_API_CLIENT_ID=mcp
WORKSPACE_HOST=adb-1.2.azuredatabricks.net
MCP_SERVICE_NAME=mcp_obo_lab.mcp.mcp_lab_mcp
MCP_SERVER_URL="https://mcp-lab-mcp-1234.azurewebsites.net/mcp"
DATABRICKS_USERNAME=user@example.com
"""


def test_read_env_file_skips_comments_and_strips_quotes(tmp_path):
    p = tmp_path / ".lab.env"
    p.write_text(FULL_ENV)
    values = read_env_file(p)
    assert values["MCP_SERVER_URL"] == "https://mcp-lab-mcp-1234.azurewebsites.net/mcp"
    assert "# lab settings" not in values


def test_load_merges_secrets_and_builds_gateway_url(tmp_path):
    (tmp_path / ".lab.env").write_text(FULL_ENV)
    (tmp_path / ".lab.secrets").write_text("BACKEND_CLIENT_SECRET=s3cret\n")
    cfg = LabConfig.load(tmp_path)
    assert cfg.backend_client_secret == "s3cret"
    assert cfg.gateway_url == "https://adb-1.2.azuredatabricks.net/ai-gateway/mcp-services/mcp_obo_lab.mcp.mcp_lab_mcp"


def test_load_lists_missing_keys(tmp_path):
    (tmp_path / ".lab.env").write_text("TENANT_ID=t\n")
    with pytest.raises(RuntimeError, match="BACKEND_CLIENT_SECRET"):
        LabConfig.load(tmp_path)
