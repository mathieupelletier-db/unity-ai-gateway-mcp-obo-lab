"""Several MCP Services behind one McpSession-shaped object: merged tool list, each call routed to its service."""
import asyncio


class McpRouter:
    def __init__(self, sessions: dict):
        self.sessions = sessions  # insertion order matters: the first service is the default for unlisted tools
        self.owners: dict[str, str] = {}

    async def list_tools(self) -> dict:
        names = list(self.sessions)
        results = await asyncio.gather(*(self.sessions[n].list_tools() for n in names))
        errors, by_tool = [], {}
        for svc, res in zip(names, results):
            if "error" in res:
                errors.append({"service": svc, "error": res["error"], "status": res.get("status")})
                continue
            for tool in res["tools"]:
                by_tool.setdefault(tool["name"], []).append((svc, tool))
        tools, self.owners = [], {}
        for name, owned in by_tool.items():
            if len(owned) > 1:
                svcs = ", ".join(s for s, _ in owned)
                errors.append({"service": svcs, "error": {"code": None, "message": f"tool name collision: {name} ({svcs})"}, "status": None})
                continue
            svc, tool = owned[0]
            self.owners[name] = svc
            tools.append(tool)
        return {"tools": tools, "services": dict(self.owners), "errors": errors}

    async def call(self, name: str, args: dict, service: str | None = None) -> dict:
        svc = service or self.owners.get(name) or next(iter(self.sessions))
        if svc not in self.sessions:
            return {"error": {"code": None, "message": f"unknown MCP service: {svc}"}, "status": None}
        return await self.sessions[svc].call(name, args)
