import time
from types import SimpleNamespace

import jwt

TENANT = "11111111-1111-1111-1111-111111111111"
AUD = "22222222-2222-2222-2222-222222222222"
ISS = f"https://login.microsoftonline.com/{TENANT}/v2.0"
LAB_BASE_URL = "https://mcp-lab-mcp.azurewebsites.net"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class FakeJwks:
    """Stands in for PyJWKClient: always returns the test public key."""

    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=self._public_key)


def make_token(private_key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISS, "aud": AUD, "iat": now, "nbf": now, "exp": now + 600, "ver": "2.0",
        "scp": "mcp.invoke", "oid": "oid-1", "preferred_username": "user@example.com",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_key, algorithm="RS256")


def rpc(method: str, params: dict | None = None, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
