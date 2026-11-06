import json

from tests.helpers import MCP_HEADERS, make_token, rpc


def auth(keypair, **claims) -> dict:
    return {**MCP_HEADERS, "Authorization": f"Bearer {make_token(keypair[0], **claims)}"}


async def test_missing_token_is_401(lab_http):
    r = await lab_http.post("/mcp", json=rpc("tools/list"), headers=MCP_HEADERS)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


async def test_invalid_token_is_401(lab_http):
    r = await lab_http.post("/mcp", json=rpc("tools/list"), headers={**MCP_HEADERS, "Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_app_service_host_header_is_accepted(lab_http, keypair):
    # Review Focus 1: FastMCP's DNS-rebinding protection must not reject *.azurewebsites.net
    r = await lab_http.post("/mcp", json=rpc("tools/list"), headers=auth(keypair))
    assert r.status_code == 200


async def test_tools_list_exposes_three_tools(lab_http, keypair):
    r = await lab_http.post("/mcp", json=rpc("tools/list"), headers=auth(keypair))
    assert {t["name"] for t in r.json()["result"]["tools"]} == {"whoami", "echo", "admin_reset"}


async def test_whoami_returns_caller_claims(lab_http, keypair):
    r = await lab_http.post(
        "/mcp",
        json=rpc("tools/call", {"name": "whoami", "arguments": {}}),
        headers=auth(keypair, oid="user-oid-42", email="jane@example.com"),
    )
    assert r.status_code == 200
    payload = json.loads(r.json()["result"]["content"][0]["text"])
    assert payload["oid"] == "user-oid-42"
    assert payload["email"] == "jane@example.com"
    assert payload["scp"] == "mcp.invoke"


async def test_echo_returns_input(lab_http, keypair):
    r = await lab_http.post("/mcp", json=rpc("tools/call", {"name": "echo", "arguments": {"text": "hello"}}), headers=auth(keypair))
    assert r.json()["result"]["content"][0]["text"] == "hello"
