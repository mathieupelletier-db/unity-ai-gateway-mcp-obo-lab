"""Thin adapter over the lab's raw MCP JSON-RPC client: one initialized session per chat turn, errors as data."""
import httpx

from client.mcp_rpc import McpRpcClient, RpcResult


def _error(r: RpcResult) -> dict:
    if r.body and "error" in r.body:
        err = r.body["error"]
        if isinstance(err, dict):
            return {"error": {**err, "message": str(err.get("message") or "")}, "status": r.status}
        return {"error": {"code": None, "message": str(err)[:300]}, "status": r.status}
    return {"error": {"code": None, "message": r.text[:300]}, "status": r.status}


def _transport_error(e: httpx.HTTPError) -> dict:
    return {"error": {"code": None, "message": f"{type(e).__name__}: {e}"[:300]}, "status": 0}


class McpSession:
    def __init__(self, url: str, bearer: str, http: httpx.AsyncClient):
        self.client = McpRpcClient(url, bearer, http)
        self._ready = False

    async def _ensure(self) -> dict | None:
        if self._ready:
            return None
        init = await self.client.initialize()
        if init.status != 200 or not init.body or "result" not in init.body:
            return _error(init)
        self._ready = True
        return None

    async def list_tools(self) -> dict:
        try:
            if err := await self._ensure():
                return err
            r = await self.client.request("tools/list")
            if r.status == 200 and r.body and "result" in r.body:
                return {"tools": r.body["result"].get("tools", [])}
            return _error(r)
        except httpx.HTTPError as e:
            return _transport_error(e)

    async def call(self, name: str, args: dict) -> dict:
        try:
            if err := await self._ensure():
                return err
            r = await self.client.request("tools/call", {"name": name, "arguments": args})
            if r.status == 200 and r.body and "result" in r.body:
                result = r.body["result"]
                text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
                if result.get("isError"):
                    return {"error": {"code": None, "message": text}, "status": 200}
                return {"text": text}
            return _error(r)
        except httpx.HTTPError as e:
            return _transport_error(e)
