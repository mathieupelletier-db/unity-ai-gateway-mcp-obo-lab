import httpx

from client.mcp_rpc import McpRpcClient, parse_body
from tests.helpers import LAB_BASE_URL, make_token


async def test_initialize_and_list_against_lab_server(lab_http, keypair):
    client = McpRpcClient(f"{LAB_BASE_URL}/mcp", make_token(keypair[0]), lab_http)
    init = await client.initialize()
    assert init.status == 200
    listed = await client.request("tools/list")
    assert listed.status == 200
    assert {t["name"] for t in listed.body["result"]["tools"]} == {"whoami", "echo", "admin_reset"}


async def test_unauthorized_is_reported_not_raised(lab_http):
    client = McpRpcClient(f"{LAB_BASE_URL}/mcp", "bad", lab_http)
    init = await client.initialize()
    assert init.status == 401
    assert init.body is None or "result" not in init.body


def test_parses_sse_body():
    # Review Focus 4: gateways may answer tools/call as text/event-stream
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n\n'
    r = httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
    assert parse_body(r) == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}


async def test_non_json_429_is_recorded():
    # Review Focus 4: a rate-limited call may come back as plain text or HTML
    transport = httpx.MockTransport(lambda req: httpx.Response(429, headers={"content-type": "text/html"}, text="<h1>Too Many Requests</h1>"))
    async with httpx.AsyncClient(transport=transport) as http:
        res = await McpRpcClient("https://gw.example/mcp", "t", http).request("tools/call", {"name": "whoami", "arguments": {}})
    assert res.status == 429
    assert res.body is None
    assert "Too Many Requests" in res.text


async def test_session_id_is_echoed_on_later_requests():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("mcp-session-id"))
        return httpx.Response(200, headers={"content-type": "application/json", "mcp-session-id": "s-1"},
                              json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = McpRpcClient("https://gw.example/mcp", "t", http)
        await client.initialize()
        await client.request("tools/list")
    assert seen[0] is None
    assert seen[-1] == "s-1"
