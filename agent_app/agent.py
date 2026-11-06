"""Tool-calling loop: LLM as the app, MCP tools as the user. Errors are data shown to the user and the model."""
import json
import re
import urllib.parse
from dataclasses import dataclass, field

from agent_app.llm import LlmError, mcp_tools_to_openai

SYSTEM_PROMPT = ("You are the MCP lab assistant. Use the provided tools when they help answer. "
                 "If a tool returns an error, explain it plainly to the user; do not retry blocked calls.")
SIGN_IN_URL = re.compile(r"https://[^\s,]+/mcp-service-login\?[^\s,]+")


@dataclass
class AgentResult:
    answer: str
    events: list[dict] = field(default_factory=list)


def tool_error_event(name: str, res: dict, trusted_host: str | None = None) -> dict:
    err = res.get("error")
    err = err if isinstance(err, dict) else {"code": None, "message": err}
    ev = {"type": "tool_error", "name": name, "status": res.get("status"), "code": err.get("code"), "message": str(err.get("message") or "")}
    # Only a -32006 (missing credential) link on the workspace host is offered as a clickable sign-in; anything else is just text.
    if ev["code"] == -32006 and trusted_host and (m := SIGN_IN_URL.search(ev["message"])):
        url = m.group(0).rstrip(".")
        if urllib.parse.urlparse(url).hostname == trusted_host.lower():
            ev["sign_in_url"] = url
    return ev


def _text(content) -> str:
    """Unity Gateway may return content as a list of blocks (e.g. reasoning + text); show only the text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = (b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text")
        return "".join(t for t in texts if isinstance(t, str))
    return ""


def _args(raw) -> dict:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _malformed(msg) -> str | None:
    if not isinstance(msg, dict):
        return "message is not an object"
    calls = msg.get("tool_calls") or []
    if not isinstance(calls, list):
        return "tool_calls is not a list"
    for c in calls:
        if not isinstance(c, dict):
            return "tool_call is not an object"
        if not isinstance(c.get("id"), str) or not c["id"]:
            return "tool_call has no id"
        fn = c.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
            return "tool_call has no function name"
    return None


async def run_agent(user_text: str, llm, mcp, max_rounds: int = 4, trusted_host: str | None = None) -> AgentResult:
    events: list[dict] = []
    listed = await mcp.list_tools()
    if "error" in listed:  # a single McpSession
        ev = tool_error_event("tools/list", listed, trusted_host)
        events.append(ev)
        hint = " Use 'Sign in to tools' and retry." if "sign_in_url" in ev else ""
        return AgentResult(f"I can't reach your tools ({ev['status']}): {ev['message'][:200]}.{hint}", events)
    for e in listed.get("errors", []):  # McpRouter: one entry per failing service (or name collision)
        events.append(tool_error_event(f"tools/list:{e['service']}", {"error": e["error"], "status": e["status"]}, trusted_host))
    if not listed["tools"] and events:
        first = events[0]
        hint = " Use 'Sign in to tools' and retry." if any("sign_in_url" in ev for ev in events) else ""
        return AgentResult(f"I can't reach your tools ({first['status']}): {first['message'][:200]}.{hint}", events)
    offered = {"type": "tools_offered", "names": [t["name"] for t in listed["tools"]]}
    if "services" in listed:
        offered["services"] = listed["services"]
    events.append(offered)
    tools = mcp_tools_to_openai(listed["tools"])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]
    for _ in range(max_rounds):
        try:
            msg = await llm.chat(messages, tools)
        except LlmError as e:
            events.append({"type": "http_error", "source": "llm", "status": e.status, "message": e.body[:300]})
            return AgentResult(f"The model call failed ({e.status}).", events)
        if reason := _malformed(msg):
            events.append({"type": "http_error", "source": "llm", "status": 200, "message": f"malformed model output: {reason}"})
            return AgentResult("The model returned a malformed response.", events)
        calls = msg.get("tool_calls") or []
        if msg.get("finish_reason") == "length":
            events.append({"type": "http_error", "source": "llm", "status": 200, "message": "model output truncated (max_tokens)"})
            if calls:  # tool arguments may be cut mid-JSON: never execute them
                return AgentResult("The model's response was cut off.", events)
        if not calls:
            events.append({"type": "final"})
            return AgentResult(_text(msg.get("content")) or "(The model returned no text.)", events)
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name, args = c["function"]["name"], _args(c["function"].get("arguments"))
            events.append({"type": "tool_call", "name": name, "arguments": args})
            res = await mcp.call(name, args)
            if "error" in res:
                ev = tool_error_event(name, res, trusted_host)
                events.append(ev)
                content = json.dumps({"error": {"status": ev["status"], "code": ev["code"], "message": ev["message"]}})
            else:
                events.append({"type": "tool_result", "name": name, "text": res["text"]})
                content = res["text"]
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
    events.append({"type": "max_rounds"})
    return AgentResult(f"I stopped after {max_rounds} tool rounds.", events)
