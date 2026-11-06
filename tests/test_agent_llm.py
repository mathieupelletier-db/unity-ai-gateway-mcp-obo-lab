import json

import httpx
import pytest

from agent_app.llm import LlmClient, LlmError, mcp_tools_to_openai


def test_mcp_tools_to_openai_maps_schema():
    tools = [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]
    assert mcp_tools_to_openai(tools) == [{"type": "function", "function": {
        "name": "echo", "description": "Echo text",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}]


def test_mcp_tools_to_openai_defaults_missing_schema():
    assert mcp_tools_to_openai([{"name": "whoami"}])[0]["function"]["parameters"] == {"type": "object", "properties": {}}


async def test_chat_posts_model_messages_tools_with_app_token():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "length"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = LlmClient("https://ws/ai-gateway/mlflow/v1", lambda: "app-token", "system.ai.claude-haiku-5-5", http)
        msg = await llm.chat([{"role": "user", "content": "x"}], [{"type": "function", "function": {"name": "whoami"}}])
    assert msg == {"role": "assistant", "content": "hi", "finish_reason": "length"}
    assert seen["body"]["max_tokens"] == 4096
    assert seen["url"] == "https://ws/ai-gateway/mlflow/v1/chat/completions"
    assert seen["auth"] == "Bearer app-token"
    assert seen["body"]["model"] == "system.ai.claude-haiku-5-5"
    assert seen["body"]["tools"][0]["function"]["name"] == "whoami"


async def test_chat_raises_llm_error_on_non_200():
    transport = httpx.MockTransport(lambda req: httpx.Response(403, text="PERMISSION_DENIED: no EXECUTE"))
    async with httpx.AsyncClient(transport=transport) as http:
        llm = LlmClient("https://ws/ai-gateway/mlflow/v1", lambda: "t", "m", http)
        with pytest.raises(LlmError) as e:
            await llm.chat([], [])
    assert e.value.status == 403 and "EXECUTE" in e.value.body


async def test_chat_raises_llm_error_on_malformed_200():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"unexpected": True}))
    async with httpx.AsyncClient(transport=transport) as http:
        llm = LlmClient("https://ws/ai-gateway/mlflow/v1", lambda: "t", "m", http)
        with pytest.raises(LlmError) as e:
            await llm.chat([], [])
    assert e.value.status == 200


async def test_chat_raises_llm_error_on_transport_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = LlmClient("https://ws/ai-gateway/mlflow/v1", lambda: "t", "m", http)
        with pytest.raises(LlmError) as e:
            await llm.chat([], [])
    assert e.value.status == 0 and "ConnectTimeout" in e.value.body


async def test_chat_without_finish_reason_returns_none():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]}))
    async with httpx.AsyncClient(transport=transport) as http:
        msg = await LlmClient("https://ws/ai-gateway/mlflow/v1", lambda: "t", "m", http).chat([], [])
    assert msg == {"role": "assistant", "content": "hi", "finish_reason": None}
