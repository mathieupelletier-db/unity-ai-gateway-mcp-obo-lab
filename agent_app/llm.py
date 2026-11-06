"""OpenAI-compatible chat completions through Unity Gateway, called with the app's own identity."""
from typing import Callable

import httpx


class LlmError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"LLM call failed {status}: {body[:200]}")
        self.status, self.body = status, body


def mcp_tools_to_openai(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t["name"],
        "description": t.get("description", ""),
        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
    }} for t in tools]


class LlmClient:
    def __init__(self, base_url: str, token_provider: Callable[[], str], model: str, http: httpx.AsyncClient):
        self.base_url, self.token_provider, self.model, self.http = base_url.rstrip("/"), token_provider, model, http

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        try:
            r = await self.http.post(
                f"{self.base_url}/chat/completions",
                json={"model": self.model, "messages": messages, "tools": tools, "max_tokens": 4096},
                headers={"Authorization": f"Bearer {self.token_provider()}"},
                timeout=120,
            )
        except httpx.HTTPError as e:
            raise LlmError(0, f"{type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise LlmError(r.status_code, r.text[:500])
        try:
            choice = r.json()["choices"][0]
            msg = dict(choice["message"])
            msg["finish_reason"] = choice.get("finish_reason")
            return msg
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LlmError(r.status_code, r.text[:500]) from e
