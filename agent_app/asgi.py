"""Uvicorn entry for App Service: `python -m uvicorn agent_app.asgi:app --host 0.0.0.0 --port 8000`."""
from agent_app.web import AppSettings, EntraTokenBroker, create_app
from mcp_server.auth import EntraJwtValidator

settings = AppSettings.from_env()
app = create_app(settings, EntraJwtValidator(settings.tenant_id, settings.backend_client_id, required_scope="access_as_user"),
                 EntraTokenBroker(settings))
