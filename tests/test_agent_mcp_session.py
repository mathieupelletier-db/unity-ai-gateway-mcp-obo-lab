import json

import httpx

from agent_app.mcp_session import McpSession

INIT = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}


def gateway(responses: dict):
    """Fake gateway keyed by JSON-RPC method; notifications get 202."""
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if "id" not in body:
            return httpx.Response(202)
        status, payload = responses[body["method"]]
        if isinstance(payload, str):
            return httpx.Response(status, text=payload, headers={"content-type": "text/plain"})
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


async def test_list_tools_returns_tools():
    t = gateway({"initialize": (200, INIT), "tools/list": (200, {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "whoami"}]}})})
    async with httpx.AsyncClient(transport=t) as http:
        assert await McpSession("https://gw/mcp", "tok", http).list_tools() == {"tools": [{"name": "whoami"}]}


async def test_call_joins_text_content():
    result = {"content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "lab"}]}
    t = gateway({"initialize": (200, INIT), "tools/call": (200, {"jsonrpc": "2.0", "id": 2, "result": result})})
    async with httpx.AsyncClient(transport=t) as http:
        assert await McpSession("https://gw/mcp", "tok", http).call("echo", {"text": "x"}) == {"text": "hello lab"}


async def test_call_returns_rpc_error():
    err = {"code": -32003, "message": "Detected sensitive data (US_SSN)."}
    t = gateway({"initialize": (200, INIT), "tools/call": (200, {"jsonrpc": "2.0", "id": 2, "error": err})})
    async with httpx.AsyncClient(transport=t) as http:
        assert await McpSession("https://gw/mcp", "tok", http).call("echo", {}) == {"error": err, "status": 200}


async def test_call_returns_non_json_429():
    t = gateway({"initialize": (200, INIT), "tools/call": (429, "Too Many Requests")})
    async with httpx.AsyncClient(transport=t) as http:
        res = await McpSession("https://gw/mcp", "tok", http).call("whoami", {})
    assert res["status"] == 429 and "Too Many Requests" in res["error"]["message"]


async def test_initialize_failure_is_returned_by_list_tools():
    err = {"code": -32006, "message": "You're not signed in to the MCP service 'c.s.svc'. Sign in at https://ws/mcp-service-login?name=c.s.svc&o=1, then retry your request."}
    t = gateway({"initialize": (401, {"jsonrpc": "2.0", "id": 1, "error": err})})
    async with httpx.AsyncClient(transport=t) as http:
        assert await McpSession("https://gw/mcp", "tok", http).list_tools() == {"error": err, "status": 401}


def timing_out(req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=req)


async def test_list_tools_transport_error_is_data():
    async with httpx.AsyncClient(transport=httpx.MockTransport(timing_out)) as http:
        res = await McpSession("https://gw/mcp", "tok", http).list_tools()
    assert res["status"] == 0 and "ConnectTimeout" in res["error"]["message"]


async def test_call_transport_error_is_data():
    async with httpx.AsyncClient(transport=httpx.MockTransport(timing_out)) as http:
        res = await McpSession("https://gw/mcp", "tok", http).call("whoami", {})
    assert res["status"] == 0 and "ConnectTimeout" in res["error"]["message"]


async def test_string_error_is_normalised():
    t = gateway({"initialize": (200, INIT), "tools/call": (401, {"error": "unauthorized", "detail": "x"})})
    async with httpx.AsyncClient(transport=t) as http:
        res = await McpSession("https://gw/mcp", "tok", http).call("whoami", {})
    assert res == {"error": {"code": None, "message": "unauthorized"}, "status": 401}


async def test_error_with_null_message_is_coerced_to_empty_string():
    t = gateway({"initialize": (200, INIT), "tools/call": (200, {"jsonrpc": "2.0", "id": 2, "error": {"code": -32603, "message": None}})})
    async with httpx.AsyncClient(transport=t) as http:
        res = await McpSession("https://gw/mcp", "tok", http).call("whoami", {})
    assert res == {"error": {"code": -32603, "message": ""}, "status": 200}
