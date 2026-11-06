from agent_app.mcp_router import McpRouter

SIGN_IN = "Sign in at https://ws/mcp-service-login?name=b&o=1, then retry"


class FakeSession:
    def __init__(self, tools=None, error=None):
        self.tools, self.error, self.calls = tools or [], error, []

    async def list_tools(self):
        return {"error": self.error, "status": 401} if self.error else {"tools": self.tools}

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"text": f"{name} ok"}


async def test_merges_tools_and_records_owners():
    r = McpRouter({"a": FakeSession([{"name": "whoami"}]), "b": FakeSession([{"name": "searchJira"}])})
    listed = await r.list_tools()
    assert [t["name"] for t in listed["tools"]] == ["whoami", "searchJira"]
    assert listed["services"] == {"whoami": "a", "searchJira": "b"} and listed["errors"] == []


async def test_call_routes_to_owner():
    a, b = FakeSession([{"name": "whoami"}]), FakeSession([{"name": "searchJira"}])
    r = McpRouter({"a": a, "b": b})
    await r.list_tools()
    assert await r.call("searchJira", {"q": "x"}) == {"text": "searchJira ok"}
    assert b.calls == [("searchJira", {"q": "x"})] and a.calls == []


async def test_partial_failure_returns_tools_and_errors():
    # Review Focus 2
    r = McpRouter({"a": FakeSession([{"name": "whoami"}]), "b": FakeSession(error={"code": -32006, "message": SIGN_IN})})
    listed = await r.list_tools()
    assert [t["name"] for t in listed["tools"]] == ["whoami"]
    assert listed["errors"] == [{"service": "b", "error": {"code": -32006, "message": SIGN_IN}, "status": 401}]


async def test_all_failing_keeps_every_error():
    r = McpRouter({"a": FakeSession(error={"code": -32006, "message": "A"}), "b": FakeSession(error={"code": -32006, "message": "B"})})
    listed = await r.list_tools()
    assert listed["tools"] == [] and [e["service"] for e in listed["errors"]] == ["a", "b"]


async def test_name_collision_withholds_tool_and_reports_it():
    # Review Focus 5
    r = McpRouter({"a": FakeSession([{"name": "search"}, {"name": "whoami"}]), "b": FakeSession([{"name": "search"}])})
    listed = await r.list_tools()
    assert [t["name"] for t in listed["tools"]] == ["whoami"]
    assert listed["errors"][0]["error"]["message"] == "tool name collision: search (a, b)"


async def test_call_hidden_tool_goes_to_named_service_or_default():
    # Review Focus 4: forced calls of unlisted tools must reach a gateway (which answers -32003), never fail locally
    a, b = FakeSession([{"name": "whoami"}]), FakeSession([{"name": "searchJira"}])
    r = McpRouter({"a": a, "b": b})
    await r.call("admin_reset", {})
    await r.call("deleteJiraIssue", {}, service="b")
    assert a.calls == [("admin_reset", {})] and b.calls == [("deleteJiraIssue", {})]


async def test_call_unknown_service_is_an_error():
    r = McpRouter({"a": FakeSession()})
    assert await r.call("x", {}, service="evil") == {"error": {"code": None, "message": "unknown MCP service: evil"}, "status": None}
