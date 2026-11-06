"""Per-user MCP credentials held by Unity Gateway: sign the caller in or out of each MCP service (Beta credential API)."""
import asyncio

import httpx


def user_credentials_url(workspace_host: str, service: str) -> str:
    return f"https://{workspace_host}/api/2.1/unity-catalog/mcp-services/{service}/user-credentials"


async def revoke_user_credentials(http: httpx.AsyncClient, workspace_host: str, services, token: str) -> list[dict]:
    """DELETE the caller's credential on each service. 404 means already signed out; one failure never stops the rest.

    The gateway may keep serving a revoked credential for a few minutes (lab: about 4) before the next sign-in prompt.
    """
    out = []
    for svc in services:
        try:
            r = await http.delete(user_credentials_url(workspace_host, svc), headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            out.append({"service": svc, "ok": False, "status": 0, "message": f"{type(e).__name__}: {e}"[:200]})
            continue
        if r.status_code in (200, 204):
            out.append({"service": svc, "ok": True, "status": r.status_code, "message": "signed out"})
        elif r.status_code == 404:
            out.append({"service": svc, "ok": True, "status": 404, "message": "not signed in"})
        else:
            out.append({"service": svc, "ok": False, "status": r.status_code, "message": r.text[:200]})
    return out


async def create_user_credential(http: httpx.AsyncClient, workspace_host: str, service: str, token: str, code: str, verifier: str,
                                 redirect_uri: str, poll_s: float = 0.5, tries: int = 20) -> dict:
    """Sign the caller in to one MCP service with an authorization code the app obtained itself (PKCE), then wait for ACTIVE.

    Databricks redeems the code at the provider with the connection's own client secret; the app never sees provider tokens.
    """
    url, headers = user_credentials_url(workspace_host, service), {"Authorization": f"Bearer {token}"}
    body = {"options": {"authorization_code": code, "pkce_verifier": verifier, "oauth_redirect_uri": redirect_uri}}
    try:
        r = await http.post(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        return {"service": service, "ok": False, "status": 0, "state": None, "message": f"{type(e).__name__}: {e}"[:300]}
    if r.status_code != 200:
        return {"service": service, "ok": False, "status": r.status_code, "state": None, "message": r.text[:300]}
    state = (r.json().get("provisioning_info") or {}).get("state")
    while state in ("PROVISIONING", "UPDATING") and tries > 0:
        tries -= 1
        await asyncio.sleep(poll_s)
        g = await http.get(url, headers=headers)
        state = (g.json().get("provisioning_info") or {}).get("state") if g.status_code == 200 else f"HTTP {g.status_code}"
    ok = state == "ACTIVE"
    return {"service": service, "ok": ok, "status": r.status_code, "state": state, "message": "signed in" if ok else f"sign-in did not complete: {state}"}
