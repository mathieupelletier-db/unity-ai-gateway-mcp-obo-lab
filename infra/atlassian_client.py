"""One-time registration of a confidential OAuth client with Atlassian's MCP authorization server (RFC 7591 endpoint).

The Unity Gateway connection then uses it for OAuth U2M per user. client_id → .lab.env, secret → .lab.secrets (mode 600).
Usage: uv run python -m infra.atlassian_client   (idempotent; never prints the secret)
"""
import os
from pathlib import Path

import httpx

from client.config import ROOT, read_env_file
from infra.lab_payloads import ATLASSIAN_REGISTER, ATLASSIAN_SCOPES

UNIFIED_HOST = "<unified-login-host>"  # sign-in can land on either host (proven in phase 1)


def registration_body(workspace_host: str, app_url: str | None = None) -> dict:
    """app_url adds the Client App's /tools-callback, for the app-driven (redirect) sign-in."""
    extra = [f"{app_url}/tools-callback"] if app_url else []
    return {
        "client_name": "mcp-lab-unity-gateway",
        "redirect_uris": [f"https://{workspace_host}/login/oauth/http.html", f"https://{UNIFIED_HOST}/login/oauth/http.html", *extra],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        "scope": ATLASSIAN_SCOPES,
    }


def parse_registration(status: int, body: dict) -> tuple[str, str]:
    if status not in (200, 201):
        raise RuntimeError(f"registration failed: HTTP {status}: {str(body)[:300]}")
    if not body.get("client_id"):
        raise RuntimeError("registration returned no client_id")
    if not body.get("client_secret"):
        raise RuntimeError(f"registration returned no client_secret (keys: {sorted(body)})")
    return body["client_id"], body["client_secret"]


def upsert_env(path: Path, key: str, value: str, secret: bool = False) -> None:
    lines = [l for l in (path.read_text().splitlines() if path.exists() else []) if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if secret else 0o644)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    if secret:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main() -> None:
    env = read_env_file(ROOT / ".lab.env")
    if env.get("ATLASSIAN_CLIENT_ID"):
        print("Atlassian client already registered; skipping")
        return
    r = httpx.post(ATLASSIAN_REGISTER, json=registration_body(env["WORKSPACE_HOST"], env.get("AGENT_APP_URL") or None), timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:300]}
    try:
        client_id, secret = parse_registration(r.status_code, body)
    except RuntimeError as e:
        raise SystemExit(str(e))
    upsert_env(ROOT / ".lab.secrets", "ATLASSIAN_CLIENT_SECRET", secret, secret=True)
    upsert_env(ROOT / ".lab.env", "ATLASSIAN_CLIENT_ID", client_id)
    print(f"Atlassian client registered: client_id {client_id[:6]}… (secret stored in .lab.secrets)")


if __name__ == "__main__":
    main()
