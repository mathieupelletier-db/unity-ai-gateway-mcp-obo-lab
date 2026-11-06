"""Uvicorn entry point for Azure App Service: `python -m uvicorn mcp_server.asgi:app`."""
import os

from mcp_server.auth import EntraJwtValidator
from mcp_server.server import build_app

app, _server = build_app(
    EntraJwtValidator(tenant_id=os.environ["ENTRA_TENANT_ID"], audience=os.environ["MCP_API_CLIENT_ID"])
)
