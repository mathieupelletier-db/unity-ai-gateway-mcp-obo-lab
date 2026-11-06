"""Lab MCP server: a sample Entra-protected MCP server backend."""
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from mcp_server.auth import AuthError, EntraJwtValidator

WHOAMI_FIELDS = ("oid", "preferred_username", "email", "name", "scp", "azp", "aud", "iss", "ver", "iat", "exp")
CALLER_CLAIMS: ContextVar[dict] = ContextVar("caller_claims", default={})


def create_server() -> FastMCP:
    # host="0.0.0.0" keeps FastMCP from auto-enabling localhost-only Host validation (Review Focus 1)
    server = FastMCP("mcp-lab-mcp", host="0.0.0.0", stateless_http=True, json_response=True)

    @server.tool()
    def whoami() -> dict:
        """Return the identity claims of the Entra token this server received."""
        claims = CALLER_CLAIMS.get()
        return {k: claims.get(k) for k in WHOAMI_FIELDS}

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back. Used to exercise guardrail policies on input and output."""
        return text

    @server.tool()
    def admin_reset() -> str:
        """Privileged tool that the MCP Service's tool selection must hide."""
        return "admin_reset executed"

    return server


class EntraAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, validator: EntraJwtValidator):
        super().__init__(app)
        self.validator = validator

    async def dispatch(self, request, call_next):
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        try:
            claims = self.validator.validate(token)
        except AuthError as e:
            return JSONResponse({"error": "unauthorized", "detail": str(e)}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        reset = CALLER_CLAIMS.set(claims)
        try:
            return await call_next(request)
        finally:
            CALLER_CLAIMS.reset(reset)


def build_app(validator: EntraJwtValidator):
    server = create_server()
    app = server.streamable_http_app()
    app.add_middleware(EntraAuthMiddleware, validator=validator)
    return app, server
