import pytest

from agent_app.agent import run_agent, tool_error_event
from agent_app.llm import LlmError

SIGN_IN = "You're not signed in to the MCP service 'c.s.svc'. Sign in at https://ws/mcp-service-login?name=c.s.svc&o=1, then retry your request."


class FakeMcp:
    def __init__(self, tools=None, results=None, list_error=None):
        self.tools = tools or [{"name": "whoami"}, {"name": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]
        self.results = results or {}
        self.list_error = list_error
        self.calls = []

    async def list_tools(self):
        return self.list_error or {"tools": self.tools}

    async def call(self, name, args):
        self.calls.append((name, args))
        return self.results.get(name, {"text": f"{name} ok"})


class FakeLlm:
    """Returns scripted assistant messages in order; records what it was sent."""
    def __init__(self, script):
        self.script, self.sent = list(script), []

    async def chat(self, messages, tools):
        self.sent.append((list(messages), tools))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def call(name, args="{}", id_="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": id_, "type": "function", "function": {"name": name, "arguments": args}}]}


async def test_answer_without_tools():
    res = await run_agent("hi", FakeLlm([{"role": "assistant", "content": "hello"}]), FakeMcp())
    assert res.answer == "hello" and res.events == [{"type": "tools_offered", "names": ["whoami", "echo"]}, {"type": "final"}]


async def test_tool_call_then_final_answer():
    llm = FakeLlm([call("whoami"), {"role": "assistant", "content": "You are jane"}])
    mcp = FakeMcp(results={"whoami": {"text": '{"email": "jane@example.com"}'}})
    res = await run_agent("who am I?", llm, mcp)
    assert res.answer == "You are jane"
    assert [e["type"] for e in res.events] == ["tools_offered", "tool_call", "tool_result", "final"]
    assert mcp.calls == [("whoami", {})]
    tool_msg = llm.sent[1][0][-1]
    assert tool_msg == {"role": "tool", "tool_call_id": "c1", "content": '{"email": "jane@example.com"}'}


async def test_tool_error_is_recorded_and_fed_back():
    # Review Focus 4 (hidden or blocked tool) and A3 (SSN guardrail)
    err = {"error": {"code": -32003, "message": "Detected sensitive data (US_SSN)."}, "status": 200}
    llm = FakeLlm([call("echo", '{"text": "SSN 123-45-6789"}'), {"role": "assistant", "content": "Blocked by policy."}])
    res = await run_agent("echo my SSN", llm, FakeMcp(results={"echo": err}))
    ev = [e for e in res.events if e["type"] == "tool_error"][0]
    assert ev["code"] == -32003 and "US_SSN" in ev["message"]
    assert "US_SSN" in llm.sent[1][0][-1]["content"]


async def test_missing_credential_returns_sign_in_url():
    # Review Focus 5 / A6
    res = await run_agent("hi", FakeLlm([]), FakeMcp(list_error={"error": {"code": -32006, "message": SIGN_IN}, "status": 401}), trusted_host="ws")
    assert res.events[0]["sign_in_url"] == "https://ws/mcp-service-login?name=c.s.svc&o=1"
    assert "sign in" in res.answer.lower()


async def test_llm_error_is_reported_not_raised():
    # Review Focus 3: the app SP lacks EXECUTE on the model service
    res = await run_agent("hi", FakeLlm([LlmError(403, "PERMISSION_DENIED")]), FakeMcp())
    assert res.events[-1] == {"type": "http_error", "source": "llm", "status": 403, "message": "PERMISSION_DENIED"}
    assert "403" in res.answer


async def test_bad_tool_arguments_are_treated_as_empty():
    # Review Focus 2
    llm = FakeLlm([call("whoami", "not json"), {"role": "assistant", "content": "done"}])
    mcp = FakeMcp()
    res = await run_agent("x", llm, mcp)
    assert mcp.calls == [("whoami", {})] and res.answer == "done"


async def test_stops_after_max_rounds():
    llm = FakeLlm([call("whoami", id_=f"c{i}") for i in range(4)])
    res = await run_agent("loop", llm, FakeMcp(), max_rounds=4)
    assert res.events[-1] == {"type": "max_rounds"} and "4" in res.answer


def test_tool_error_event_without_link():
    ev = tool_error_event("whoami", {"error": {"message": "Too Many Requests"}, "status": 429})
    assert ev == {"type": "tool_error", "name": "whoami", "status": 429, "code": None, "message": "Too Many Requests"}


async def test_malformed_tool_call_is_reported_not_raised():
    llm = FakeLlm([{"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "whoami", "arguments": "{}"}}]}])
    mcp = FakeMcp()
    res = await run_agent("x", llm, mcp)
    assert res.events[-1]["type"] == "http_error" and res.events[-1]["source"] == "llm"
    assert "malformed" in res.events[-1]["message"] and mcp.calls == []


async def test_non_dict_message_is_reported_not_raised():
    res = await run_agent("x", FakeLlm([None]), FakeMcp())
    assert res.events[-1]["type"] == "http_error" and "malformed" in res.events[-1]["message"]


# --- C1: list-shaped assistant content ---

BLOCKS = [{"type": "reasoning", "summary": "thinking"}, {"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}]


async def test_list_content_final_answer_is_joined_text():
    res = await run_agent("hi", FakeLlm([{"role": "assistant", "content": BLOCKS}]), FakeMcp())
    assert isinstance(res.answer, str) and res.answer == "Hello world"


async def test_list_content_on_tool_round_is_passed_back_verbatim():
    first = {"role": "assistant", "content": BLOCKS, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "whoami", "arguments": "{}"}}]}
    llm = FakeLlm([first, {"role": "assistant", "content": "done"}])
    await run_agent("x", llm, FakeMcp())
    history = llm.sent[1][0]
    assistant = [m for m in history if m["role"] == "assistant"][0]
    assert assistant["content"] == BLOCKS


@pytest.mark.parametrize("content", [[], [{"type": "reasoning", "summary": "x"}], None, 5], ids=["empty", "no-text-blocks", "none", "other"])
async def test_empty_or_textless_content_gets_placeholder(content):
    res = await run_agent("hi", FakeLlm([{"role": "assistant", "content": content}]), FakeMcp())
    assert res.answer == "(The model returned no text.)"


# --- M1: truncated model output ---

TRUNC = {"type": "http_error", "source": "llm", "status": 200, "message": "model output truncated (max_tokens)"}


async def test_length_with_tool_calls_executes_nothing():
    cut = {**call("whoami"), "finish_reason": "length"}
    mcp = FakeMcp()
    res = await run_agent("x", FakeLlm([cut]), mcp)
    assert mcp.calls == [] and TRUNC in res.events
    assert res.answer == "The model's response was cut off."


async def test_length_without_tool_calls_returns_partial_text():
    res = await run_agent("x", FakeLlm([{"role": "assistant", "content": "partial ans", "finish_reason": "length"}]), FakeMcp())
    assert res.answer == "partial ans" and TRUNC in res.events and res.events[-1] == {"type": "final"}


async def test_finish_reason_is_not_echoed_into_history():
    first = {**call("whoami"), "finish_reason": "tool_calls"}
    llm = FakeLlm([first, {"role": "assistant", "content": "done", "finish_reason": "stop"}])
    await run_agent("x", llm, FakeMcp())
    assistant = [m for m in llm.sent[1][0] if m["role"] == "assistant"][0]
    assert "finish_reason" not in assistant


# --- I3: sign-in link only for -32006 on the workspace host ---

def test_sign_in_url_requires_code_32006():
    res = {"error": {"code": -32003, "message": SIGN_IN}, "status": 200}
    assert "sign_in_url" not in tool_error_event("echo", res, trusted_host="ws")


def test_sign_in_url_requires_trusted_host():
    res = {"error": {"code": -32006, "message": SIGN_IN.replace("https://ws/", "https://evil.example/")}, "status": 401}
    assert "sign_in_url" not in tool_error_event("echo", res, trusted_host="ws")
    assert "sign_in_url" not in tool_error_event("echo", {"error": {"code": -32006, "message": SIGN_IN}, "status": 401})  # no trusted host given


def test_sign_in_url_set_for_32006_on_workspace_host():
    res = {"error": {"code": -32006, "message": SIGN_IN}, "status": 401}
    assert tool_error_event("echo", res, trusted_host="ws")["sign_in_url"] == "https://ws/mcp-service-login?name=c.s.svc&o=1"


async def test_run_agent_passes_trusted_host_to_tool_errors():
    err = {"error": {"code": -32006, "message": SIGN_IN}, "status": 401}
    llm = FakeLlm([call("echo"), {"role": "assistant", "content": "sign in first"}])
    res = await run_agent("x", llm, FakeMcp(results={"echo": err}), trusted_host="ws")
    assert [e for e in res.events if e["type"] == "tool_error"][0]["sign_in_url"].startswith("https://ws/")


# --- I4: error-shape normalisation ---

def test_tool_error_event_tolerates_none_message():
    ev = tool_error_event("echo", {"error": {"code": -32003, "message": None}, "status": 200})
    assert ev["message"] == ""


def test_tool_error_event_tolerates_non_dict_error():
    ev = tool_error_event("echo", {"error": "unauthorized", "status": 401})
    assert ev["message"] == "unauthorized" and ev["code"] is None


# --- I5: tools_offered debug event ---

async def test_tools_offered_lists_exactly_what_mcp_listed():
    mcp = FakeMcp(tools=[{"name": "whoami"}, {"name": "echo"}])  # admin_reset exists server-side but is not listed for this user
    res = await run_agent("hi", FakeLlm([{"role": "assistant", "content": "ok"}]), mcp)
    assert res.events[0] == {"type": "tools_offered", "names": ["whoami", "echo"]}
    assert "admin_reset" not in res.events[0]["names"]


async def test_no_tools_offered_event_when_listing_fails():
    res = await run_agent("hi", FakeLlm([]), FakeMcp(list_error={"error": {"code": None, "message": "boom"}, "status": 500}))
    assert [e["type"] for e in res.events] == ["tool_error"]


SIGN_IN_B = "You're not signed in to the MCP service 'c.s.b'. Sign in at https://ws/mcp-service-login?name=c.s.b&o=1, then retry your request."
SIGN_IN_A = "You're not signed in to the MCP service 'c.s.a'. Sign in at https://ws/mcp-service-login?name=c.s.a&o=1, then retry your request."


class FakeRouter(FakeMcp):
    def __init__(self, listed, **kw):
        super().__init__(**kw)
        self.listed = listed

    async def list_tools(self):
        return self.listed


async def test_partial_failure_keeps_other_service_tools():
    # Review Focus 2
    listed = {"tools": [{"name": "whoami"}], "services": {"whoami": "c.s.a"},
              "errors": [{"service": "c.s.b", "error": {"code": -32006, "message": SIGN_IN_B}, "status": 401}]}
    res = await run_agent("hi", FakeLlm([{"role": "assistant", "content": "hello"}]), FakeRouter(listed), trusted_host="ws")
    assert res.events[0]["name"] == "tools/list:c.s.b" and res.events[0]["sign_in_url"].endswith("name=c.s.b&o=1")
    assert res.events[1] == {"type": "tools_offered", "names": ["whoami"], "services": {"whoami": "c.s.a"}}
    assert res.answer == "hello"


async def test_all_services_failing_returns_every_sign_in_link():
    # Review Focus 3
    listed = {"tools": [], "services": {}, "errors": [
        {"service": "c.s.a", "error": {"code": -32006, "message": SIGN_IN_A}, "status": 401},
        {"service": "c.s.b", "error": {"code": -32006, "message": SIGN_IN_B}, "status": 401}]}
    res = await run_agent("hi", FakeLlm([]), FakeRouter(listed), trusted_host="ws")
    assert [e["name"] for e in res.events] == ["tools/list:c.s.a", "tools/list:c.s.b"]
    assert all("sign_in_url" in e for e in res.events) and "sign in" in res.answer.lower()


async def test_tools_offered_has_no_services_key_for_a_plain_session():
    res = await run_agent("hi", FakeLlm([{"role": "assistant", "content": "x"}]), FakeMcp())
    assert "services" not in res.events[0]
