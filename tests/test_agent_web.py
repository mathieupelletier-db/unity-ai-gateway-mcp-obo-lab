import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agent_app.agent import AgentResult
from agent_app.llm import LlmError
from agent_app.web import AppSettings, EntraTokenBroker, ToolSignIn, create_app
from client.tokens import decode_unverified
from mcp_server.auth import EntraJwtValidator
from tests.helpers import TENANT, FakeJwks, make_token

BACKEND = "33333333-3333-3333-3333-333333333333"
SETTINGS = AppSettings(TENANT, BACKEND, "s", "fe-id", "ws.example", "c.s.svc", "system.ai.claude-haiku-5-5")


class FakeBroker:
    def obo(self, user_token):
        return jwt.encode({"upn": "jane@example.com", "aud": "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"}, "k" * 32, algorithm="HS256")

    def app_token(self):
        return jwt.encode({"appid": BACKEND, "idtyp": "app"}, "k" * 32, algorithm="HS256")


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def client(key):
    validator = EntraJwtValidator(TENANT, BACKEND, required_scope="access_as_user", jwks_client=FakeJwks(key.public_key()))

    async def runner(text, obo_token, app_token_provider):
        app_token_provider()
        return AgentResult(f"echo: {text}", [{"type": "final"}])

    app = create_app(SETTINGS, validator, FakeBroker(), runner=runner, egress_probe=lambda: {"login.microsoftonline.com": 200, "ws.example": 200})
    return TestClient(app)


def bearer(key, **claims):
    token = make_token(key, **{"aud": BACKEND, "scp": "access_as_user", "email": "jane@example.com", **claims})
    return {"Authorization": f"Bearer {token}"}


def test_config_exposes_public_settings_only(client):
    # .default so a first-time user's consent prompt also covers the backend's downstream (Azure Databricks) permission
    assert client.get("/api/config").json() == {"tenantId": TENANT, "frontendClientId": "fe-id", "backendScope": f"api://{BACKEND}/.default", "signInTools": []}


def test_chat_requires_bearer(client):
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_chat_rejects_wrong_scope(client, key):
    assert client.post("/api/chat", json={"message": "hi"}, headers=bearer(key, scp="other")).status_code == 401


def test_chat_returns_answer_events_and_identity(client, key):
    body = client.post("/api/chat", json={"message": "hi"}, headers=bearer(key)).json()
    assert body["answer"] == "echo: hi" and body["events"] == [{"type": "final"}]
    assert body["identity"] == {"user": "jane@example.com", "tools_as": "jane@example.com", "llm_as": f"app {BACKEND}"}


def test_egress_reports_probe(client):
    # Review Focus 1: VNet egress diagnostic
    assert client.get("/api/egress").json() == {"login.microsoftonline.com": 200, "ws.example": 200}


class FailingBroker(FakeBroker):
    def __init__(self, obo_error=None, app_error=None):
        self.obo_error, self.app_error = obo_error, app_error

    def obo(self, user_token):
        if self.obo_error:
            raise self.obo_error
        return super().obo(user_token)

    def app_token(self):
        if self.app_error:
            raise self.app_error
        return super().app_token()


class DownJwks:
    def get_signing_key_from_jwt(self, token):
        raise jwt.PyJWKClientConnectionError("down")


async def _echo_runner(text, obo_token, app_token_provider):
    app_token_provider()
    return AgentResult(f"echo: {text}", [{"type": "final"}])


def make_client(key, broker=None, runner=None, jwks=None):
    validator = EntraJwtValidator(TENANT, BACKEND, required_scope="access_as_user", jwks_client=jwks or FakeJwks(key.public_key()))
    app = create_app(SETTINGS, validator, broker or FakeBroker(), runner=runner or _echo_runner, egress_probe=lambda: {})
    return TestClient(app)


@pytest.mark.parametrize("error", [RuntimeError("invalid_grant: AADSTS65001 consent required"),
                                   ConnectionError("login.microsoftonline.com unreachable")], ids=["msal", "network"])
def test_chat_obo_failure_returns_auth_error(key, error):
    client = make_client(key, broker=FailingBroker(obo_error=error))
    res = client.post("/api/chat", json={"message": "hi"}, headers=bearer(key))
    assert res.status_code == 200
    assert res.json()["events"][0]["type"] == "auth_error"
    assert str(error) in res.json()["events"][0]["message"]


def test_runner_receives_obo_token_not_inbound_bearer(key):
    seen = {}

    async def runner(text, obo_token, app_token_provider):
        seen["obo"] = obo_token
        return AgentResult("ok", [])

    headers = bearer(key)
    make_client(key, runner=runner).post("/api/chat", json={"message": "hi"}, headers=headers)
    inbound = headers["Authorization"].removeprefix("Bearer ")
    assert seen["obo"] != inbound
    assert decode_unverified(seen["obo"])["upn"] == "jane@example.com"


def test_app_token_failure_becomes_llm_error(key):
    seen = {}

    async def runner(text, obo_token, app_token_provider):
        try:
            app_token_provider()
        except LlmError as e:
            seen["status"], seen["body"] = e.status, e.body
        return AgentResult("ok", [])

    broker = FailingBroker(app_error=RuntimeError("invalid_client: AADSTS7000215"))
    res = make_client(key, broker=broker, runner=runner).post("/api/chat", json={"message": "hi"}, headers=bearer(key))
    assert res.status_code == 200
    assert seen["status"] == 0 and "AADSTS7000215" in seen["body"]
    assert res.json()["identity"]["llm_as"] == "app (not used this turn)"


def test_tool_requires_bearer(key):
    assert make_client(key).post("/api/tool", json={"name": "whoami"}).status_code == 401


def test_tool_obo_failure_is_returned_as_error(key):
    broker = FailingBroker(obo_error=RuntimeError("AADSTS50076 MFA required"))
    res = make_client(key, broker=broker).post("/api/tool", json={"name": "whoami", "arguments": {}}, headers=bearer(key))
    assert res.status_code == 200
    assert "AADSTS50076" in res.json()["error"]["message"]


def test_jwks_unreachable_returns_503(key):
    res = make_client(key, jwks=DownJwks()).post("/api/chat", json={"message": "hi"}, headers=bearer(key))
    assert res.status_code == 503


def test_broker_defers_msal_until_first_use(monkeypatch):
    calls = []

    class FakeApp:
        def acquire_token_for_client(self, scopes):
            return {"access_token": "t"}

    def factory(*args, **kwargs):
        calls.append(1)
        return FakeApp()

    monkeypatch.setattr("agent_app.web.msal.ConfidentialClientApplication", factory)
    broker = EntraTokenBroker(SETTINGS)
    assert len(calls) == 0
    assert broker.app_token() == "t"
    assert len(calls) == 1
    broker.app_token()
    assert len(calls) == 1


def test_index_loads_vendored_msal_and_chat(client):
    html = client.get("/").text
    assert '<script src="/static/msal-browser.min.js"></script>' in html
    assert 'id="log"' in html and "window.__lastTurn" in html


def test_vendored_msal_is_pinned(client):
    assert client.get("/static/msal-browser.min.js").text.startswith("/*! @azure/msal-browser v4.30.0")


def test_ui_retries_401_with_forced_refresh(client):
    html = client.get("/").text
    assert "r.status === 401" in html and "forceRefresh" in html


def test_ui_resets_last_turn_and_guards_failures(client):
    html = client.get("/").text
    assert "window.__lastTurn = {status: null, body: null}" in html
    assert "turn failed:" in html and "startup failed:" in html


def test_ui_tolerates_non_json_body(client):
    assert "non-JSON response" in client.get("/").text


def test_ui_renders_only_string_answers(client):
    assert "typeof body.answer === 'string'" in client.get("/").text


def test_ui_sign_in_link_is_noopener(client):
    assert "noopener noreferrer" in client.get("/").text


def test_ui_has_debug_tool_form_and_tools_offered(client):
    html = client.get("/").text
    assert 'id="dbg"' in html and "'/api/tool'" in html
    assert 'id="tname"' in html and 'id="targs"' in html
    assert "tools_offered: " in html


def test_version_reports_dev_without_build_marker(client):
    assert client.get("/api/version").json() == {"build": "dev"}


def test_version_reports_build_marker(client, monkeypatch, tmp_path):
    marker = tmp_path / "BUILD"
    marker.write_text("agent-20261030123010\n")
    monkeypatch.setattr("agent_app.web.BUILD_FILE", marker)
    assert client.get("/api/version").json() == {"build": "agent-20261030123010"}


def test_real_runner_passes_workspace_host_as_trusted_host(key, monkeypatch):
    seen = {}

    async def fake_run_agent(text, llm, mcp, **kwargs):
        seen.update(kwargs)
        return AgentResult("ok", [])

    monkeypatch.setattr("agent_app.web.run_agent", fake_run_agent)
    validator = EntraJwtValidator(TENANT, BACKEND, required_scope="access_as_user", jwks_client=FakeJwks(key.public_key()))
    client = TestClient(create_app(SETTINGS, validator, FakeBroker(), egress_probe=lambda: {}))
    assert client.post("/api/chat", json={"message": "hi"}, headers=bearer(key)).status_code == 200
    assert seen == {"trusted_host": "ws.example"}


def test_service_names_fall_back_to_single_service():
    assert SETTINGS.service_names == ("c.s.svc",)
    two = AppSettings(TENANT, BACKEND, "s", "fe-id", "ws.example", "c.s.svc", "m", mcp_service_names=("c.s.a", "c.s.b"))
    assert two.service_names == ("c.s.a", "c.s.b")
    assert two.mcp_url_for("c.s.b") == "https://ws.example/ai-gateway/mcp-services/c.s.b"


def test_from_env_reads_mcp_service_names(monkeypatch):
    for k, v in {"ENTRA_TENANT_ID": "t", "BACKEND_CLIENT_ID": "b", "BACKEND_CLIENT_SECRET": "s", "FRONTEND_CLIENT_ID": "f",
                 "WORKSPACE_HOST": "ws.example", "MCP_SERVICE_NAME": "c.s.a", "LLM_MODEL": "m", "MCP_SERVICE_NAMES": "c.s.a, c.s.b"}.items():
        monkeypatch.setenv(k, v)
    assert AppSettings.from_env().service_names == ("c.s.a", "c.s.b")


def test_tool_rejects_unknown_service(key):
    # Review Focus 4: only configured services are reachable from the debug panel
    body = make_client(key).post("/api/tool", json={"name": "x", "service": "evil.s.x"}, headers=bearer(key)).json()
    assert body == {"error": {"code": None, "message": "unknown MCP service: evil.s.x"}, "status": None}


def test_real_runner_builds_one_session_per_service(key, monkeypatch):
    from agent_app.mcp_router import McpRouter
    seen = {}

    async def fake_run_agent(text, llm, mcp, **kwargs):
        seen["mcp"] = mcp
        return AgentResult("ok", [])

    monkeypatch.setattr("agent_app.web.run_agent", fake_run_agent)
    settings = AppSettings(TENANT, BACKEND, "s", "fe-id", "ws.example", "c.s.a", "m", mcp_service_names=("c.s.a", "c.s.b"))
    validator = EntraJwtValidator(TENANT, BACKEND, required_scope="access_as_user", jwks_client=FakeJwks(key.public_key()))
    client = TestClient(create_app(settings, validator, FakeBroker(), egress_probe=lambda: {}))
    assert client.post("/api/chat", json={"message": "hi"}, headers=bearer(key)).status_code == 200
    assert isinstance(seen["mcp"], McpRouter) and list(seen["mcp"].sessions) == ["c.s.a", "c.s.b"]


def test_ui_groups_tools_by_service(client):
    html = client.get("/").text
    assert "ev.services" in html and "tools_offered: " in html


def test_ui_keeps_one_sign_in_link_per_service(client):
    html = client.get("/").text
    assert "$('signin').replaceChildren();" in html and "'tools/list:'" in html and "$('signin').appendChild(a)" in html and "addSignIn(ev.sign_in_url" in html


def test_ui_debug_panel_has_service_field(client):
    html = client.get("/").text
    assert 'id="tsvc"' in html and "service: svc" in html


def test_tool_routes_by_name_without_service(key, monkeypatch):
    # Final review I1: a visible tool of the second service must not be sent to the first service
    calls = []

    class FakeSession:
        def __init__(self, url, bearer, http):
            self.url = url

        async def list_tools(self):
            return {"tools": [{"name": "whoami"}]} if self.url.endswith("c.s.a") else {"tools": [{"name": "<ATLASSIAN_USER_TOOL>"}]}

        async def call(self, name, args):
            calls.append((self.url.rsplit("/", 1)[-1], name))
            return {"text": "ok"}

    monkeypatch.setattr("agent_app.web.McpSession", FakeSession)
    settings = AppSettings(TENANT, BACKEND, "s", "fe-id", "ws.example", "c.s.a", "m", mcp_service_names=("c.s.a", "c.s.b"))
    validator = EntraJwtValidator(TENANT, BACKEND, required_scope="access_as_user", jwks_client=FakeJwks(key.public_key()))
    client = TestClient(create_app(settings, validator, FakeBroker(), egress_probe=lambda: {}))
    assert client.post("/api/tool", json={"name": "<ATLASSIAN_USER_TOOL>"}, headers=bearer(key)).json() == {"text": "ok"}
    assert calls == [("c.s.b", "<ATLASSIAN_USER_TOOL>")]


def test_ui_opens_sign_in_as_a_popup_with_opener(client):
    # Final review I2: the gateway's OAuth callback needs window.opener, so the sign-in must be a child popup
    assert "window.open(url, 'mcp-login'" in client.get("/").text


def test_ui_offers_consent_on_aadsts65001(client):
    html = client.get("/").text
    assert "AADSTS65001" in html and "prompt: 'consent'" in html and "Grant consent" in html


def test_ui_auth_error_omits_missing_status_and_code(client):
    assert "[ev.status, ev.code].filter(x => x !== undefined && x !== null)" in client.get("/").text


def test_tools_requires_bearer(client):
    assert client.get("/api/tools").status_code == 401


def test_tools_lists_each_service_and_its_errors(key, monkeypatch):
    sign = "You're not signed in to the MCP service 'c.s.b'. Sign in at https://ws.example/mcp-service-login?name=c.s.b&o=1, then retry your request."

    class FakeSession:
        def __init__(self, url, bearer, http):
            self.url = url

        async def list_tools(self):
            if self.url.endswith("c.s.a"):
                return {"tools": [{"name": "whoami", "description": "Who am I"}]}
            return {"error": {"code": -32006, "message": sign}, "status": 401}

        async def call(self, name, args):
            return {"text": "x"}

    monkeypatch.setattr("agent_app.web.McpSession", FakeSession)
    settings = AppSettings(TENANT, BACKEND, "s", "fe-id", "ws.example", "c.s.a", "m", mcp_service_names=("c.s.a", "c.s.b"))
    validator = EntraJwtValidator(TENANT, BACKEND, required_scope="access_as_user", jwks_client=FakeJwks(key.public_key()))
    body = TestClient(create_app(settings, validator, FakeBroker(), egress_probe=lambda: {})).get("/api/tools", headers=bearer(key)).json()
    assert body["tools"] == [{"name": "whoami", "service": "c.s.a", "description": "Who am I"}]
    err = body["errors"][0]
    assert (err["service"], err["code"], err["status"]) == ("c.s.b", -32006, 401)
    assert err["sign_in_url"] == "https://ws.example/mcp-service-login?name=c.s.b&o=1"


def test_tools_obo_failure_is_reported(key):
    class FailBroker(FakeBroker):
        def obo(self, user_token):
            raise RuntimeError("invalid_grant: AADSTS65001 consent required")

    body = make_client(key, broker=FailBroker()).get("/api/tools", headers=bearer(key)).json()
    assert body["tools"] == [] and "AADSTS65001" in body["errors"][0]["message"]


def test_ui_has_grouped_tool_dropdown(client):
    html = client.get("/").text
    assert 'id="tsel"' in html and "fetch('/api/tools'" in html and "createElement('optgroup')" in html and "$('tsvc').value = v.service" in html


def test_disconnect_requires_bearer(client):
    assert client.post("/api/tools/disconnect").status_code == 401


def test_disconnect_revokes_each_service_as_the_user(key, monkeypatch):
    calls = []

    async def fake_revoke(http, host, services, token):
        calls.append((host, services, decode_unverified(token)["upn"]))
        return [{"service": s, "ok": True, "status": 200, "message": "signed out"} for s in services]

    monkeypatch.setattr("agent_app.web.revoke_user_credentials", fake_revoke)
    settings = AppSettings(TENANT, BACKEND, "s", "fe-id", "ws.example", "c.s.a", "m", mcp_service_names=("c.s.a", "c.s.b"))
    validator = EntraJwtValidator(TENANT, BACKEND, required_scope="access_as_user", jwks_client=FakeJwks(key.public_key()))
    body = TestClient(create_app(settings, validator, FakeBroker(), egress_probe=lambda: {})).post("/api/tools/disconnect", headers=bearer(key)).json()
    assert calls == [("ws.example", ("c.s.a", "c.s.b"), "jane@example.com")]  # the OBO token, not the inbound bearer
    assert [r["service"] for r in body["results"]] == ["c.s.a", "c.s.b"]


def test_disconnect_obo_failure_is_reported(key):
    class FailBroker(FakeBroker):
        def obo(self, user_token):
            raise RuntimeError("invalid_grant: AADSTS65001 consent required")

    body = make_client(key, broker=FailBroker()).post("/api/tools/disconnect", headers=bearer(key)).json()
    assert body["results"] == [] and "AADSTS65001" in body["error"]


def test_signout_clears_site_data(client):
    r = client.post("/api/signout")
    assert r.status_code == 204
    assert all(f'"{d}"' in r.headers["clear-site-data"] for d in ("cache", "cookies", "storage"))


def test_ui_has_sign_out_and_disconnect_buttons(client):
    html = client.get("/").text
    assert 'id="signout"' in html and "fetch('/api/signout'" in html and "pca.logoutRedirect(" in html
    assert 'id="disconnect"' in html and "fetch('/api/tools/disconnect'" in html


ENTRA_AUTH = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
SIGN_IN = (ToolSignIn("c.s.a", "conn-client", "https://t.onmicrosoft.com/api/mcp.invoke offline_access", ENTRA_AUTH),
           ToolSignIn("c.s.b", "atl-client", "read:me offline_access", "https://auth.atlassian.com/authorize", prompt="consent", resource="https://mcp.atlassian.com/v2/mcp"))


def silent_client(key, **kw):
    settings = AppSettings(TENANT, BACKEND, "s", "fe-id", "ws.example", "c.s.a", "m", mcp_service_names=("c.s.a", "c.s.b", "c.s.c"), tool_signin=SIGN_IN)
    validator = EntraJwtValidator(TENANT, BACKEND, required_scope="access_as_user", jwks_client=FakeJwks(key.public_key()))
    return TestClient(create_app(settings, validator, kw.get("broker") or FakeBroker(), egress_probe=lambda: {}))


def test_config_lists_app_sign_in_tools(key):
    assert silent_client(key).get("/api/config").json()["signInTools"] == [
        {"service": "c.s.a", "clientId": "conn-client", "scope": "https://t.onmicrosoft.com/api/mcp.invoke offline_access",
         "authorizeUrl": ENTRA_AUTH, "prompt": "none", "resource": ""},
        {"service": "c.s.b", "clientId": "atl-client", "scope": "read:me offline_access",
         "authorizeUrl": "https://auth.atlassian.com/authorize", "prompt": "consent", "resource": "https://mcp.atlassian.com/v2/mcp"}]


def test_from_env_reads_tool_signin_json(monkeypatch):
    spec = ('[{"service": "c.s.a", "client_id": "cc", "scope": "x offline_access", "authorize_url": "https://e/authorize"},'
            ' {"service": "c.s.b", "client_id": "ac", "scope": "y", "authorize_url": "https://a/authorize", "prompt": "consent", "resource": "https://r"}]')
    for k, v in {"ENTRA_TENANT_ID": "t", "BACKEND_CLIENT_ID": "b", "BACKEND_CLIENT_SECRET": "s", "FRONTEND_CLIENT_ID": "f", "WORKSPACE_HOST": "ws.example",
                 "MCP_SERVICE_NAME": "c.s.a", "LLM_MODEL": "m", "TOOL_SIGNIN_JSON": spec}.items():
        monkeypatch.setenv(k, v)
    assert AppSettings.from_env().tool_signin == (ToolSignIn("c.s.a", "cc", "x offline_access", "https://e/authorize"),
                                                  ToolSignIn("c.s.b", "ac", "y", "https://a/authorize", prompt="consent", resource="https://r"))


def test_tools_callback_serves_the_app(client):
    r = client.get("/tools-callback?code=abc&state=xyz")
    assert r.status_code == 200 and "MCP lab agent" in r.text


CONNECT = {"service": "c.s.a", "code": "the-code", "verifier": "the-verifier", "redirect_uri": "https://testserver/tools-callback"}


def test_connect_requires_bearer(key):
    assert silent_client(key).post("/api/tools/connect", json=CONNECT).status_code == 401


def test_connect_creates_the_credential_as_the_user(key, monkeypatch):
    calls = []

    async def fake_create(http, host, service, token, code, verifier, redirect_uri):
        calls.append((host, service, decode_unverified(token)["upn"], code, verifier, redirect_uri))
        return {"service": service, "ok": True, "status": 200, "state": "ACTIVE", "message": "signed in"}

    monkeypatch.setattr("agent_app.web.create_user_credential", fake_create)
    body = silent_client(key).post("/api/tools/connect", json=CONNECT, headers=bearer(key)).json()
    assert calls == [("ws.example", "c.s.a", "jane@example.com", "the-code", "the-verifier", "https://testserver/tools-callback")]
    assert (body["ok"], body["state"]) == (True, "ACTIVE")


@pytest.mark.parametrize("change", [{"service": "c.s.c"}, {"redirect_uri": "https://evil.example/tools-callback"}, {"code": ""}],
                         ids=["no-app-sign-in", "foreign-redirect", "missing-code"])
def test_connect_rejects_bad_requests(key, change):
    r = silent_client(key).post("/api/tools/connect", json={**CONNECT, **change}, headers=bearer(key))
    assert r.status_code == 400 and r.json()["ok"] is False


def test_connect_obo_failure_is_reported(key):
    class FailBroker(FakeBroker):
        def obo(self, user_token):
            raise RuntimeError("invalid_grant: AADSTS65001 consent required")

    body = silent_client(key, broker=FailBroker()).post("/api/tools/connect", json=CONNECT, headers=bearer(key)).json()
    assert body["ok"] is False and "AADSTS65001" in body["message"]


def test_ui_runs_an_app_driven_pkce_code_flow(client):
    html = client.get("/").text
    for s in ("cfg.signInTools", "prompt: tool.prompt", "code_challenge_method: 'S256'", "crypto.subtle.digest('SHA-256'",
              "'/tools-callback'", "fetch('/api/tools/connect'", "q.set('resource', tool.resource)", "q.set('login_hint', account.username)"):
        assert s in html, s


def test_ui_silent_services_start_automatically_and_others_get_a_connect_button(client):
    html = client.get("/").text
    assert "tool.prompt === 'none'" in html and "addConnect(" in html and "'Connect ' +" in html
    assert "window.open(url, 'mcp-login'" in html  # the gateway popup stays for services without app sign-in


def test_ui_opens_the_tool_sign_in_popup_automatically_once(client):
    html = client.get("/").text
    assert "function autoPopup(" in html and "sessionStorage.getItem('popupTried:' + service)" in html
    assert "if (!w)" in html and "pendingPopup" in html  # blocked on load -> retried inside the first Send click


def test_ui_refreshes_tools_when_the_sign_in_popup_closes(client):
    assert "w.closed" in client.get("/").text
