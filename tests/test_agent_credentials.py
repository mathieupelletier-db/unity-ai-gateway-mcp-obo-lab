import json

import httpx

from agent_app.credentials import create_user_credential, revoke_user_credentials, user_credentials_url


def recorder(statuses: dict):
    """A transport answering each service's DELETE with the given status (or raising), recording every request."""
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        status = next(v for k, v in statuses.items() if f"/mcp-services/{k}/" in req.url.path)
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, json={"error_code": "NOT_FOUND"} if status == 404 else {})
    return httpx.MockTransport(handler), seen


def test_user_credentials_url_is_the_uc_mcp_service_path():
    assert user_credentials_url("ws.example", "c.s.a") == "https://ws.example/api/2.1/unity-catalog/mcp-services/c.s.a/user-credentials"


async def test_revoke_deletes_each_service_as_the_caller():
    transport, seen = recorder({"c.s.a": 200, "c.s.b": 204})
    async with httpx.AsyncClient(transport=transport) as http:
        out = await revoke_user_credentials(http, "ws.example", ("c.s.a", "c.s.b"), "user-obo-token")
    assert [(r.method, r.url.path, r.headers["authorization"]) for r in seen] == [
        ("DELETE", "/api/2.1/unity-catalog/mcp-services/c.s.a/user-credentials", "Bearer user-obo-token"),
        ("DELETE", "/api/2.1/unity-catalog/mcp-services/c.s.b/user-credentials", "Bearer user-obo-token")]
    assert [(o["service"], o["ok"], o["message"]) for o in out] == [("c.s.a", True, "signed out"), ("c.s.b", True, "signed out")]


async def test_revoke_treats_404_as_already_signed_out():
    transport, _ = recorder({"c.s.a": 404})
    async with httpx.AsyncClient(transport=transport) as http:
        (out,) = await revoke_user_credentials(http, "ws.example", ("c.s.a",), "t")
    assert (out["ok"], out["status"], out["message"]) == (True, 404, "not signed in")


async def test_revoke_keeps_going_after_a_failing_service():
    transport, seen = recorder({"c.s.a": httpx.ConnectError("boom"), "c.s.b": 403, "c.s.c": 200})
    async with httpx.AsyncClient(transport=transport) as http:
        out = await revoke_user_credentials(http, "ws.example", ("c.s.a", "c.s.b", "c.s.c"), "t")
    assert len(seen) == 3
    assert [(o["service"], o["ok"], o["status"]) for o in out] == [("c.s.a", False, 0), ("c.s.b", False, 403), ("c.s.c", True, 200)]
    assert "ConnectError" in out[0]["message"]


def creator(post_status=200, states=("PROVISIONING", "ACTIVE")):
    """POST answers with the first state, each later GET with the next one; records the requests."""
    seen, it = [], iter(states)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "POST" and post_status != 200:
            return httpx.Response(post_status, json={"error_code": "INVALID_PARAMETER_VALUE", "message": "bad code"})
        return httpx.Response(200, json={"options": {}, "provisioning_info": {"state": next(it)}})
    return httpx.MockTransport(handler), seen


async def test_create_posts_the_oauth_exchange_fields_and_waits_for_active():
    transport, seen = creator()
    async with httpx.AsyncClient(transport=transport) as http:
        out = await create_user_credential(http, "ws.example", "c.s.a", "obo", "the-code", "the-verifier", "https://app/tools-callback", poll_s=0)
    post = seen[0]
    assert (post.method, post.url.path, post.headers["authorization"]) == ("POST", "/api/2.1/unity-catalog/mcp-services/c.s.a/user-credentials", "Bearer obo")
    assert json.loads(post.content) == {"options": {"authorization_code": "the-code", "pkce_verifier": "the-verifier", "oauth_redirect_uri": "https://app/tools-callback"}}
    assert seen[1].method == "GET"
    assert (out["service"], out["ok"], out["state"]) == ("c.s.a", True, "ACTIVE")


async def test_create_reports_a_rejected_exchange():
    transport, _ = creator(post_status=400)
    async with httpx.AsyncClient(transport=transport) as http:
        out = await create_user_credential(http, "ws.example", "c.s.a", "obo", "c", "v", "https://app/tools-callback", poll_s=0)
    assert (out["ok"], out["status"], out["state"]) == (False, 400, None) and "bad code" in out["message"]


async def test_create_stops_polling_on_failed_state():
    transport, seen = creator(states=("PROVISIONING", "FAILED"))
    async with httpx.AsyncClient(transport=transport) as http:
        out = await create_user_credential(http, "ws.example", "c.s.a", "obo", "c", "v", "https://app/tools-callback", poll_s=0)
    assert (out["ok"], out["state"]) == (False, "FAILED") and len(seen) == 2
