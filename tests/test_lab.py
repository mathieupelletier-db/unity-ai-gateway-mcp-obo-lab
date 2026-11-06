import pytest
import json

from client.config import LabConfig
from client.lab import count_statuses, credential_state, redact, step, whoami_problems, write_evidence
from client.mcp_rpc import RpcResult

CFG = LabConfig("t", "fe", "be", "secret", "mcp-api-id", "ws", "c.s.svc", "https://x/mcp", "user@example.com")


def test_redact_masks_sensitive_keys_recursively():
    assert redact({"token": "x", "nested": [{"client_secret": "y", "ok": 1}]}) == {
        "token": "<redacted>", "nested": [{"client_secret": "<redacted>", "ok": 1}]}


def test_count_statuses():
    assert count_statuses([200, 429, 200, 429, 429]) == {"200": 2, "429": 3}


def test_whoami_problems_clean_payload():
    payload = {"aud": "mcp-api-id", "email": "User@Example.com", "scp": "mcp.invoke"}
    assert whoami_problems(payload, CFG) == []


def test_whoami_problems_flags_email_mismatch():
    # Review Focus 3: B2B UPN-style identity must not pass as the Databricks user
    payload = {"aud": "mcp-api-id", "email": "user_example.com#EXT#@<your-tenant>.onmicrosoft.com", "scp": "mcp.invoke"}
    assert any("email" in p for p in whoami_problems(payload, CFG))


def test_whoami_problems_flags_wrong_audience_and_scope():
    problems = whoami_problems({"aud": "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d", "email": CFG.databricks_username, "scp": ""}, CFG)
    assert len(problems) == 2


def test_credential_state_parsing():
    # Review Focus 5: only ACTIVE counts as signed in
    assert credential_state(200, {"provisioning_info": {"state": "ACTIVE"}}) == "ACTIVE"
    assert credential_state(200, {"provisioning_info": {"state": "PROVISIONING"}}) == "PROVISIONING"
    assert credential_state(404, None) == "NOT_FOUND"
    assert credential_state(403, {"error_code": "PERMISSION_DENIED"}) == "HTTP_403"


def test_step_extracts_tools_text_and_errors():
    listed = step("tools/list", RpcResult(200, {"result": {"tools": [{"name": "b"}, {"name": "a"}]}}, ""))
    assert listed["tools"] == ["a", "b"]
    called = step("tools/call", RpcResult(200, {"result": {"isError": True, "content": [{"type": "text", "text": "blocked"}]}}, ""))
    assert called["is_error"] is True and called["text"] == "blocked"
    failed = step("tools/call", RpcResult(429, None, "Too Many Requests"))
    assert failed == {"method": "tools/call", "status": 429, "raw": "Too Many Requests"}


def test_write_evidence_appends_redacted_jsonl(tmp_path):
    path = tmp_path / "evidence.jsonl"
    write_evidence("t1", {"outcome": "accepted", "token": "eyJ..."}, path)
    write_evidence("t2", {"outcome": "accepted"}, path)
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert [l["test"] for l in lines] == ["t1", "t2"]
    assert lines[0]["token"] == "<redacted>"


def test_whoami_payload_parses_long_tool_text():
    # Final review I3: tool text over 500 chars must still parse, or whoami_problems falsely fails
    from client.lab import whoami_payload
    payload = {"aud": "mcp-api-id", "email": "user@example.com", "scp": "mcp.invoke", "name": "x" * 600}
    text = json.dumps(payload)
    steps = [step("tools/call", RpcResult(200, {"result": {"content": [{"type": "text", "text": text}]}}, ""))]
    assert len(text) > 600
    assert whoami_payload(steps) == payload
    assert whoami_problems(whoami_payload(steps), CFG) == []


def test_write_evidence_adds_tag_only_when_given(tmp_path):
    path = tmp_path / "evidence.jsonl"
    write_evidence("t1", {"outcome": "accepted"}, path, tag="phase2")
    write_evidence("t1", {"outcome": "accepted"}, path)
    first, second = [json.loads(l) for l in path.read_text().splitlines()]
    assert first["tag"] == "phase2"
    assert "tag" not in second


def test_parser_accepts_tag_and_defaults_to_none():
    from client.lab import build_parser
    args = build_parser().parse_args(["t1", "--inbound", "entra", "--tag", "phase2"])
    assert (args.cmd, args.inbound, args.tag) == ("t1", "entra", "phase2")
    assert build_parser().parse_args(["t3"]).tag is None


def test_init_outcome_treats_rpc_error_as_not_accepted():
    # Spike finding: the gateway answers HTTP 200 with a JSON-RPC error when the upstream is unreachable
    from client.lab import init_outcome
    ok = step("initialize", RpcResult(200, {"result": {"protocolVersion": "2025-06-18"}}, ""))
    rpc_err = step("initialize", RpcResult(200, {"error": {"code": -32603, "message": "temporarily unavailable"}}, ""))
    http_err = step("initialize", RpcResult(401, None, "unauthorized"))
    assert init_outcome(ok) == "accepted"
    assert init_outcome(rpc_err) == "rpc_error_-32603"
    assert init_outcome(http_err) == "rejected_401"


def test_init_outcome_requires_a_result_on_200():
    # Final review I2: a 200 with a non-JSON body or a bare envelope must not count as accepted
    from client.lab import init_outcome
    html = step("initialize", RpcResult(200, None, "<html>maintenance</html>"))
    bare = step("initialize", RpcResult(200, {"jsonrpc": "2.0", "id": 1}, ""))
    assert init_outcome(html) == "no_result_200"
    assert init_outcome(bare) == "no_result_200"


def test_parser_accepts_tools_and_call_commands():
    from client.lab import build_parser
    a = build_parser().parse_args(["call", "--tool", "getJiraIssue", "--args", '{"issueIdOrKey": "LAB-1"}', "--tag", "atlassian"])
    assert (a.cmd, a.tool, a.args, a.tag) == ("call", "getJiraIssue", '{"issueIdOrKey": "LAB-1"}', "atlassian")
    assert build_parser().parse_args(["tools"]).args == "{}"


def test_call_without_tool_exits_before_any_network():
    from client.lab import run
    with pytest.raises(SystemExit, match="--tool"):
        run("call", None, "entra", 1, tag=None, tool=None)
