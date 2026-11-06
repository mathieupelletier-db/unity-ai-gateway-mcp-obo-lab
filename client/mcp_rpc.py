"""Minimal MCP Streamable HTTP JSON-RPC client that reports HTTP status codes (the MCP SDK raises instead)."""
import json
from dataclasses import dataclass

import httpx

PROTOCOL_VERSION = "2025-06-18"


@dataclass
class RpcResult:
    status: int
    body: dict | None  # parsed JSON-RPC message, or None when the body isn't JSON-RPC
    text: str          # raw body (first 500 chars) for evidence


def parse_body(response: httpx.Response) -> dict | None:
    ctype = response.headers.get("content-type", "")
    try:
        if ctype.startswith("text/event-stream"):
            data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
            return json.loads(data[-1]) if data else None
        if ctype.startswith("application/json"):
            return response.json()
    except ValueError:
        return None
    return None


class McpRpcClient:
    def __init__(self, url: str, bearer: str, http: httpx.AsyncClient):
        self.url = url
        self.http = http
        self.headers = {
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        self._next_id = 0

    async def _post(self, payload: dict) -> RpcResult:
        r = await self.http.post(self.url, json=payload, headers=self.headers)
        if sid := r.headers.get("mcp-session-id"):
            self.headers["Mcp-Session-Id"] = sid
        return RpcResult(r.status_code, parse_body(r), r.text[:500])

    async def request(self, method: str, params: dict | None = None) -> RpcResult:
        self._next_id += 1
        return await self._post({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}})

    async def initialize(self) -> RpcResult:
        res = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcp-lab-client", "version": "0.1"},
        })
        if res.status == 200 and res.body and "result" in res.body:
            self.headers["MCP-Protocol-Version"] = res.body["result"].get("protocolVersion", PROTOCOL_VERSION)
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res
