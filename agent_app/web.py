"""Client App backend: validates the user's token, calls tools as the user (OBO) and the LLM as the app."""
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt
import msal
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agent_app.agent import run_agent, tool_error_event
from agent_app.credentials import create_user_credential, revoke_user_credentials
from agent_app.llm import LlmClient, LlmError
from agent_app.mcp_router import McpRouter
from agent_app.mcp_session import McpSession
from client.tokens import AZURE_DATABRICKS_SCOPE, access_token_from, decode_unverified
from mcp_server.auth import AuthError, EntraJwtValidator

STATIC = Path(__file__).parent / "static"
BUILD_FILE = Path(__file__).parent / "BUILD"  # written by infra/61_agent_app.sh; absent in dev
log = logging.getLogger("agent_app")


@dataclass(frozen=True)
class ToolSignIn:
    """App-driven OAuth sign-in for one MCP Service: the app gets the code, Databricks redeems it (Beta credential API).

    prompt "none" signs in silently (Entra SSO); any other prompt (Atlassian: "consent") runs as a full-page redirect.
    """
    service: str
    client_id: str
    scope: str
    authorize_url: str
    prompt: str = "none"
    resource: str = ""


@dataclass(frozen=True)
class AppSettings:
    tenant_id: str
    backend_client_id: str
    backend_client_secret: str
    frontend_client_id: str
    workspace_host: str
    mcp_service_name: str
    llm_model: str
    mcp_service_names: tuple[str, ...] = ()
    tool_signin: tuple[ToolSignIn, ...] = ()

    @property
    def service_names(self) -> tuple[str, ...]:
        return self.mcp_service_names or (self.mcp_service_name,)

    def mcp_url_for(self, name: str) -> str:
        return f"https://{self.workspace_host}/ai-gateway/mcp-services/{name}"

    @property
    def mcp_url(self) -> str:
        return self.mcp_url_for(self.service_names[0])

    @property
    def llm_base_url(self) -> str:
        return f"https://{self.workspace_host}/ai-gateway/mlflow/v1"

    @classmethod
    def from_env(cls) -> "AppSettings":
        e = os.environ
        names = tuple(s.strip() for s in e.get("MCP_SERVICE_NAMES", "").split(",") if s.strip())
        signin = tuple(ToolSignIn(s["service"], s["client_id"], s["scope"], s["authorize_url"], s.get("prompt", "none"), s.get("resource", ""))
                       for s in json.loads(e.get("TOOL_SIGNIN_JSON") or "[]"))
        return cls(e["ENTRA_TENANT_ID"], e["BACKEND_CLIENT_ID"], e["BACKEND_CLIENT_SECRET"], e["FRONTEND_CLIENT_ID"],
                   e["WORKSPACE_HOST"], e["MCP_SERVICE_NAME"], e["LLM_MODEL"], mcp_service_names=names, tool_signin=signin)


class EntraTokenBroker:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._app = None

    @property
    def app(self) -> msal.ConfidentialClientApplication:
        # MSAL does OIDC discovery in its constructor; deferring it lets the app boot and serve /api/egress without egress.
        if self._app is None:
            self._app = msal.ConfidentialClientApplication(self.settings.backend_client_id, client_credential=self.settings.backend_client_secret,
                                                           authority=f"https://login.microsoftonline.com/{self.settings.tenant_id}")
        return self._app

    def obo(self, user_token: str) -> str:
        return access_token_from(self.app.acquire_token_on_behalf_of(user_assertion=user_token, scopes=[AZURE_DATABRICKS_SCOPE]))

    def app_token(self) -> str:
        return access_token_from(self.app.acquire_token_for_client(scopes=[AZURE_DATABRICKS_SCOPE]))


def _who(token: str, *keys: str) -> str:
    claims = decode_unverified(token)
    return next((claims[k] for k in keys if claims.get(k)), "unknown")


def default_egress_probe(settings: AppSettings) -> dict:
    out = {}
    for host in ("login.microsoftonline.com", settings.workspace_host):
        try:
            out[host] = httpx.get(f"https://{host}/", timeout=10, follow_redirects=False).status_code
        except httpx.HTTPError as e:
            out[host] = f"error: {type(e).__name__}"
    return out


def create_app(settings: AppSettings, validator: EntraJwtValidator, broker, runner=None, egress_probe=None) -> FastAPI:
    app = FastAPI(title="MCP lab agent")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def router_for(obo_token: str, http: httpx.AsyncClient) -> McpRouter:
        return McpRouter({name: McpSession(settings.mcp_url_for(name), obo_token, http) for name in settings.service_names})

    async def real_runner(text: str, obo_token: str, app_token_provider):
        async with httpx.AsyncClient(timeout=120) as http:
            mcp = router_for(obo_token, http)
            llm = LlmClient(settings.llm_base_url, app_token_provider, settings.llm_model, http)
            return await run_agent(text, llm, mcp, trusted_host=settings.workspace_host)

    run = runner or real_runner
    probe = egress_probe or (lambda: default_egress_probe(settings))

    def user_claims(request: Request) -> dict | JSONResponse:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        try:
            return {**validator.validate(token), "token": token}
        except AuthError as e:
            log.warning("bearer rejected: %s", e)
            if isinstance(e.__cause__, jwt.PyJWKClientConnectionError):
                return JSONResponse({"detail": "token validation unavailable: signing keys unreachable"}, status_code=503)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/tools-callback")
    def tools_callback():
        # The provider (Entra silently, Atlassian after consent) returns the authorization code here; the page hands it to /api/tools/connect.
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    def config():
        return {"tenantId": settings.tenant_id, "frontendClientId": settings.frontend_client_id,
                "backendScope": f"api://{settings.backend_client_id}/.default",
                "signInTools": [{"service": s.service, "clientId": s.client_id, "scope": s.scope, "authorizeUrl": s.authorize_url,
                                 "prompt": s.prompt, "resource": s.resource} for s in settings.tool_signin]}

    @app.get("/api/version")
    def version():
        return {"build": BUILD_FILE.read_text().strip() if BUILD_FILE.exists() else "dev"}

    @app.get("/api/egress")
    def egress():
        return probe()

    @app.post("/api/chat")
    async def chat(request: Request):
        claims = user_claims(request)
        if isinstance(claims, JSONResponse):
            return claims
        message = (await request.json()).get("message", "")
        try:
            obo_token = broker.obo(claims["token"])
        except Exception as e:  # MSAL error (RuntimeError), network (requests), bad config (ValueError)
            msg = f"{type(e).__name__}: {e}"[:300]
            return JSONResponse({"answer": f"Sign-in to Databricks failed: {msg}", "events": [{"type": "auth_error", "message": msg}]}, status_code=200)
        app_tokens: list[str] = []

        def app_token_provider() -> str:
            if not app_tokens:
                try:
                    app_tokens.append(broker.app_token())
                except Exception as e:
                    raise LlmError(0, f"app token: {type(e).__name__}: {e}"[:300]) from e
            return app_tokens[0]

        result = await run(message, obo_token, app_token_provider)
        identity = {"user": claims.get("email") or claims.get("preferred_username") or claims.get("oid"),
                    "tools_as": _who(obo_token, "upn", "email", "unique_name"),
                    "llm_as": f"app {_who(app_tokens[0], 'appid', 'azp')}" if app_tokens else "app (not used this turn)"}
        return {"answer": result.answer, "events": result.events, "identity": identity}

    @app.get("/api/tools")
    async def tools(request: Request):
        """The tools each configured MCP Service offers this user (via Unity Gateway), for the UI's tool picker."""
        claims = user_claims(request)
        if isinstance(claims, JSONResponse):
            return claims
        try:
            obo_token = broker.obo(claims["token"])
        except Exception as e:
            return {"tools": [], "errors": [{"service": None, "status": None, "code": None, "message": f"OBO failed: {type(e).__name__}: {e}"[:300]}]}
        async with httpx.AsyncClient(timeout=60) as http:
            listed = await router_for(obo_token, http).list_tools()
        tools = [{"name": t["name"], "service": listed["services"][t["name"]], "description": t.get("description") or ""} for t in listed["tools"]]
        errors = []
        for e in listed["errors"]:
            ev = tool_error_event(f"tools/list:{e['service']}", {"error": e["error"], "status": e["status"]}, settings.workspace_host)
            errors.append({"service": e["service"], **{k: ev[k] for k in ("status", "code", "message", "sign_in_url") if k in ev}})
        return {"tools": tools, "errors": errors}

    @app.post("/api/tools/disconnect")
    async def disconnect_tools(request: Request):
        """Sign this user out of every configured MCP Service: deletes their per-user credentials held by Unity Gateway."""
        claims = user_claims(request)
        if isinstance(claims, JSONResponse):
            return claims
        try:
            obo_token = broker.obo(claims["token"])
        except Exception as e:
            return {"results": [], "error": f"OBO failed: {type(e).__name__}: {e}"[:300]}
        async with httpx.AsyncClient(timeout=30) as http:
            return {"results": await revoke_user_credentials(http, settings.workspace_host, settings.service_names, obo_token)}

    @app.post("/api/tools/connect")
    async def connect_tool(request: Request):
        """Finish an app-driven sign-in: Databricks redeems the user's code for that MCP Service's per-user credential."""
        claims = user_claims(request)
        if isinstance(claims, JSONResponse):
            return claims
        body = await request.json()
        service, code, verifier, redirect_uri = (body.get(k) or "" for k in ("service", "code", "verifier", "redirect_uri"))
        if service not in {s.service for s in settings.tool_signin}:
            return JSONResponse({"ok": False, "message": f"app sign-in is not configured for {service or 'this service'}"}, status_code=400)
        if redirect_uri != f"https://{request.headers.get('host', '')}/tools-callback":
            return JSONResponse({"ok": False, "message": "redirect_uri must be this app's /tools-callback"}, status_code=400)
        if not code or not verifier:
            return JSONResponse({"ok": False, "message": "code and verifier are required"}, status_code=400)
        try:
            obo_token = broker.obo(claims["token"])
        except Exception as e:
            return {"ok": False, "service": service, "message": f"OBO failed: {type(e).__name__}: {e}"[:300]}
        async with httpx.AsyncClient(timeout=30) as http:
            return await create_user_credential(http, settings.workspace_host, service, obo_token, code, verifier, redirect_uri)

    @app.post("/api/signout")
    def signout():
        # The browser wipes this origin's cookies, storage (MSAL's token cache) and cache; the UI then ends the Entra session.
        return Response(status_code=204, headers={"Clear-Site-Data": '"cache", "cookies", "storage"'})

    @app.post("/api/tool")
    async def tool(request: Request):
        claims = user_claims(request)
        if isinstance(claims, JSONResponse):
            return claims
        body = await request.json()
        service = body.get("service") or None
        if service and service not in settings.service_names:
            return {"error": {"code": None, "message": f"unknown MCP service: {service}"}, "status": None}
        try:
            obo_token = broker.obo(claims["token"])
        except Exception as e:
            return {"error": {"code": None, "message": f"OBO failed: {type(e).__name__}: {e}"[:300]}, "status": None}
        async with httpx.AsyncClient(timeout=60) as http:
            router = router_for(obo_token, http)
            if not service and len(settings.service_names) > 1:
                await router.list_tools()  # learn which service owns the tool; unlisted names still go to the first service
            return await router.call(body["name"], body.get("arguments", {}), service=service)

    return app
