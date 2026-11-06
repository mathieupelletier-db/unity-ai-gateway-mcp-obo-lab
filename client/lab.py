"""Run the MCP/OBO lab tests and append evidence to results/evidence.jsonl.

Usage: uv run python -m client.lab <cmd> [--inbound entra|federated] [--calls N]
"""
import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import httpx

from client.config import ROOT, LabConfig
from client.mcp_rpc import McpRpcClient, RpcResult
from client.tokens import AZURE_DATABRICKS_SCOPE, decode_unverified, exchange_for_databricks_token, get_user_token, obo_token

EVIDENCE = ROOT / "results" / "evidence.jsonl"
CLAIM_FIELDS = ("aud", "iss", "ver", "scp", "azp", "appid", "oid", "email", "preferred_username", "upn", "exp")
SENSITIVE_KEYS = {"token", "access_token", "authorization", "secret", "client_secret", "assertion"}
LIST = ("tools/list", {})
WHOAMI = ("tools/call", {"name": "whoami", "arguments": {}})


def redact(value):
    if isinstance(value, dict):
        return {k: ("<redacted>" if k.lower() in SENSITIVE_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def count_statuses(statuses: list[int]) -> dict[str, int]:
    return {str(k): v for k, v in sorted(Counter(statuses).items())}


def whoami_problems(payload: dict, cfg: LabConfig) -> list[str]:
    """Check that the MCP server saw the end user's own Entra token, issued for the MCP API."""
    problems = []
    if payload.get("aud") != cfg.mcp_api_client_id:
        problems.append(f"aud {payload.get('aud')!r} != MCP API {cfg.mcp_api_client_id!r}")
    if (payload.get("email") or "").lower() != cfg.databricks_username.lower():
        problems.append(f"email {payload.get('email')!r} != {cfg.databricks_username!r}")
    if "mcp.invoke" not in (payload.get("scp") or "").split():
        problems.append("scp lacks mcp.invoke")
    return problems


def credential_state(status: int, body: dict | None) -> str:
    if status == 404:
        return "NOT_FOUND"
    if status != 200:
        return f"HTTP_{status}"
    return ((body or {}).get("provisioning_info") or {}).get("state", "UNKNOWN")


def step(method: str, res: RpcResult, params: dict | None = None) -> dict:
    out: dict = {"method": method, "status": res.status}
    if params:
        out["params"] = params
    result = (res.body or {}).get("result")
    if result is not None:
        if "tools" in result:
            out["tools"] = sorted(t["name"] for t in result["tools"])
        if "content" in result:
            out["is_error"] = result.get("isError", False)
            out["text"] = result["content"][0].get("text", "") if result["content"] else ""
    elif res.body and "error" in res.body:
        out["rpc_error"] = res.body["error"]
    else:
        out["raw"] = res.text
    return out


def init_outcome(init_step: dict) -> str:
    """'accepted' only when initialize returned a JSON-RPC result; the gateway can answer HTTP 200 with an error."""
    if init_step["status"] != 200:
        return f"rejected_{init_step['status']}"
    if "rpc_error" in init_step:
        return f"rpc_error_{(init_step['rpc_error'] or {}).get('code')}"
    if "raw" in init_step:  # 200 without a JSON-RPC result (non-JSON body, empty SSE, bare envelope)
        return "no_result_200"
    return "accepted"


def write_evidence(test: str, record: dict, path: Path = EVIDENCE, tag: str | None = None) -> dict:
    entry = {"test": test, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **({"tag": tag} if tag else {}), **redact(record)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(json.dumps(entry, indent=2, default=str))
    return entry


def claim_summary(token: str) -> dict:
    claims = decode_unverified(token)
    return {k: claims[k] for k in CLAIM_FIELDS if k in claims}


def user_token(cfg: LabConfig) -> str:
    return get_user_token(cfg.tenant_id, cfg.frontend_client_id, cfg.backend_client_id, ROOT / ".lab.msal-cache.json")


def entra_obo(cfg: LabConfig, scope: str) -> str:
    return obo_token(cfg.tenant_id, cfg.backend_client_id, cfg.backend_client_secret, user_token(cfg), scope)


def gateway_bearer(cfg: LabConfig, inbound: str) -> str:
    if inbound == "entra":
        return entra_obo(cfg, AZURE_DATABRICKS_SCOPE)
    return exchange_for_databricks_token(cfg.workspace_host, user_token(cfg))


async def call_sequence(url: str, bearer: str, calls: list[tuple[str, dict]]) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as http:
        client = McpRpcClient(url, bearer, http)
        init = await client.initialize()
        steps = [step("initialize", init)]
        if init.status != 200:
            return steps
        for method, params in calls:
            steps.append(step(method, await client.request(method, params), params))
        return steps


def whoami_payload(steps: list[dict]) -> dict:
    last = steps[-1]
    try:
        return json.loads(last.get("text") or "{}")
    except json.JSONDecodeError:
        return {}


def run(cmd: str, cfg: LabConfig, inbound: str, calls: int, tag: str | None = None, tool: str | None = None, args: str = "{}") -> dict:
    def ev(test: str, record: dict) -> dict:
        return write_evidence(test, record, tag=tag)

    if cmd == "call" and not tool:
        raise SystemExit("call needs --tool NAME")
    if cmd == "tools":  # any MCP Service: initialize + tools/list
        steps = asyncio.run(call_sequence(cfg.gateway_url, gateway_bearer(cfg, inbound), [LIST]))
        return ev("tools", {"inbound": inbound, "service": cfg.mcp_service_name, "steps": steps, "outcome": init_outcome(steps[0])})
    if cmd == "call":  # any MCP Service: one tools/call
        params = {"name": tool, "arguments": json.loads(args)}
        steps = asyncio.run(call_sequence(cfg.gateway_url, gateway_bearer(cfg, inbound), [("tools/call", params)]))
        return ev("call", {"inbound": inbound, "service": cfg.mcp_service_name, "steps": steps, "outcome": init_outcome(steps[0])})
    if cmd == "claims":
        return ev("claims", {
            "user_token_claims": claim_summary(user_token(cfg)),
            "obo_azure_databricks_claims": claim_summary(entra_obo(cfg, AZURE_DATABRICKS_SCOPE)),
        })
    if cmd == "direct":  # baseline path without the gateway: backend -> MCP server directly
        steps = asyncio.run(call_sequence(cfg.mcp_server_url, entra_obo(cfg, f"api://{cfg.mcp_api_client_id}/mcp.invoke"), [LIST, WHOAMI]))
        return ev("direct", {"steps": steps, "whoami_problems": whoami_problems(whoami_payload(steps), cfg)})
    if cmd in ("t1", "t2"):
        bearer = gateway_bearer(cfg, "entra" if cmd == "t1" else "federated")
        record = {"inbound": "entra" if cmd == "t1" else "federated", "bearer_claims": claim_summary(bearer)}
        if cmd == "t2":
            me = httpx.get(f"https://{cfg.workspace_host}/api/2.0/preview/scim/v2/Me", headers={"Authorization": f"Bearer {bearer}"}, timeout=30)
            record["scim_me"] = {"status": me.status_code, "userName": me.json().get("userName") if me.status_code == 200 else me.text[:300]}
        record["steps"] = asyncio.run(call_sequence(cfg.gateway_url, bearer, [LIST, WHOAMI]))
        record["outcome"] = init_outcome(record["steps"][0])
        return ev(cmd, record)
    if cmd == "t3-status":
        r = httpx.get(f"https://{cfg.workspace_host}/api/2.1/unity-catalog/mcp-services/{cfg.mcp_service_name}/user-credentials",
                      headers={"Authorization": f"Bearer {gateway_bearer(cfg, inbound)}"}, timeout=30)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else None
        return ev("t3-status", {"inbound": inbound, "http_status": r.status_code, "state": credential_state(r.status_code, body)})
    if cmd == "t3":
        steps = asyncio.run(call_sequence(cfg.gateway_url, gateway_bearer(cfg, inbound), [WHOAMI]))
        payload = whoami_payload(steps)
        return ev("t3", {"inbound": inbound, "steps": steps, "whoami": payload, "whoami_problems": whoami_problems(payload, cfg)})
    if cmd == "t4-ratelimit":
        steps = asyncio.run(call_sequence(cfg.gateway_url, gateway_bearer(cfg, inbound), [WHOAMI] * calls))
        statuses = [s["status"] for s in steps[1:]]
        return ev("t4-ratelimit", {"inbound": inbound, "calls": calls, "statuses": count_statuses(statuses)})
    if cmd == "t4-tools":
        steps = asyncio.run(call_sequence(cfg.gateway_url, gateway_bearer(cfg, inbound),
                                          [LIST, ("tools/call", {"name": "admin_reset", "arguments": {}})]))
        return ev("t4-tools", {"inbound": inbound, "steps": steps})
    if cmd == "t4-pii":
        steps = asyncio.run(call_sequence(cfg.gateway_url, gateway_bearer(cfg, inbound), [
            ("tools/call", {"name": "echo", "arguments": {"text": "Customer SSN is 123-45-6789"}}),
            ("tools/call", {"name": "echo", "arguments": {"text": "hello from the lab"}}),
        ]))
        return ev("t4-pii", {"inbound": inbound, "steps": steps})
    raise SystemExit(f"unknown command {cmd}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=["claims", "direct", "t1", "t2", "t3-status", "t3", "t4-ratelimit", "t4-tools", "t4-pii", "tools", "call"])
    parser.add_argument("--inbound", choices=["entra", "federated"], default="federated")
    parser.add_argument("--calls", type=int, default=30)
    parser.add_argument("--tag", default=None, help="label added to every evidence record, e.g. phase2")
    parser.add_argument("--tool", default=None, help="tool name for `call`")
    parser.add_argument("--args", default="{}", help="JSON arguments for `call`")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run(args.cmd, LabConfig.load(), args.inbound, args.calls, args.tag, tool=args.tool, args=args.args)


if __name__ == "__main__":
    main()
