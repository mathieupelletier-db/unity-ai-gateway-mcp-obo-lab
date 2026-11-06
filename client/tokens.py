"""Token acquisition for the lab's 'Client App Front-end' and 'Client App Backend' stand-ins."""
from pathlib import Path

import httpx
import jwt
import msal

AZURE_DATABRICKS_SCOPE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"  # AzureDatabricks first-party app


def access_token_from(result: dict) -> str:
    if "access_token" not in result:
        raise RuntimeError(f"{result.get('error')}: {result.get('error_description')}")
    return result["access_token"]


def _authority(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}"


def get_user_token(tenant_id: str, frontend_client_id: str, backend_client_id: str, cache_path: Path) -> str:
    """Sign the user in (interactive once, then silent from cache) and return a token for the agent backend API."""
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())
    app = msal.PublicClientApplication(frontend_client_id, authority=_authority(tenant_id), token_cache=cache)
    scopes = [f"api://{backend_client_id}/.default"]
    accounts = app.get_accounts()
    result = app.acquire_token_silent(scopes, account=accounts[0]) if accounts else None
    if not result:
        result = app.acquire_token_interactive(scopes=scopes, prompt="select_account")
    if cache.has_state_changed:
        cache_path.write_text(cache.serialize())
        cache_path.chmod(0o600)
    return access_token_from(result)


def obo_token(tenant_id: str, backend_client_id: str, backend_client_secret: str, user_token: str, scope: str) -> str:
    """The agent backend exchanges the user's token for a downstream token (Entra On-Behalf-Of)."""
    app = msal.ConfidentialClientApplication(backend_client_id, client_credential=backend_client_secret,
                                             authority=_authority(tenant_id))
    return access_token_from(app.acquire_token_on_behalf_of(user_assertion=user_token, scopes=[scope]))


def exchange_for_databricks_token(workspace_host: str, user_token: str, http: httpx.Client | None = None) -> str:
    """Databricks OAuth token federation (RFC 8693). No client_id, so the token maps to the user."""
    client = http or httpx.Client(timeout=30)
    r = client.post(f"https://{workspace_host}/oidc/v1/token", data={
        "subject_token": user_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "scope": "all-apis",
    })
    if r.status_code != 200:
        raise RuntimeError(f"token exchange failed {r.status_code}: {r.text[:500]}")
    return r.json()["access_token"]


def decode_unverified(token: str) -> dict:
    """Read claims for evidence. Never use the result for authorization decisions."""
    return jwt.decode(token, options={"verify_signature": False, "verify_aud": False, "verify_exp": False})
