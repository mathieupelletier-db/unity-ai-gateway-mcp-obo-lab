"""Validate Microsoft Entra ID v2 access tokens for the lab MCP server."""
import jwt
from jwt import PyJWKClient


class AuthError(Exception):
    """Raised when a bearer token is missing or invalid."""


class EntraJwtValidator:
    def __init__(self, tenant_id: str, audience: str, required_scope: str = "mcp.invoke", jwks_client=None):
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.audience = audience  # v2 tokens carry the API app's client ID (GUID) in aud
        self.required_scope = required_scope
        self._jwks = jwks_client or PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True
        )

    def validate(self, token: str) -> dict:
        if not token:
            raise AuthError("missing bearer token")
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer)
        except jwt.PyJWTError as e:
            raise AuthError(f"invalid token: {e}") from e
        if self.required_scope not in claims.get("scp", "").split():
            raise AuthError(f"token lacks scope {self.required_scope}")
        return claims
