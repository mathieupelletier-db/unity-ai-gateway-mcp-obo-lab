import os
import stat

import pytest

from infra.atlassian_client import parse_registration, registration_body, upsert_env
from infra.lab_payloads import ATLASSIAN_SCOPES


def test_registration_body_is_confidential_with_both_redirect_hosts():
    b = registration_body("adb-1.2.azuredatabricks.net")
    assert b["redirect_uris"] == ["https://adb-1.2.azuredatabricks.net/login/oauth/http.html",
                                  "https://<unified-login-host>/login/oauth/http.html"]
    assert b["token_endpoint_auth_method"] == "client_secret_post"
    assert b["grant_types"] == ["authorization_code", "refresh_token"] and b["response_types"] == ["code"]
    assert b["scope"] == ATLASSIAN_SCOPES and b["client_name"] == "mcp-lab-unity-gateway"


def test_parse_registration_requires_a_confidential_client():
    assert parse_registration(201, {"client_id": "c1", "client_secret": "s1"}) == ("c1", "s1")
    with pytest.raises(RuntimeError, match="no client_secret"):
        parse_registration(201, {"client_id": "c1"})
    with pytest.raises(RuntimeError, match="HTTP 400"):
        parse_registration(400, {"error": "invalid_redirect_uri"})


def test_upsert_env_replaces_key_and_keeps_others(tmp_path):
    p = tmp_path / ".lab.env"
    p.write_text("A=1\nATLASSIAN_CLIENT_ID=old\nB=2\n")
    upsert_env(p, "ATLASSIAN_CLIENT_ID", "new")
    assert p.read_text().splitlines() == ["A=1", "B=2", "ATLASSIAN_CLIENT_ID=new"]


def test_upsert_env_secret_file_is_mode_600(tmp_path):
    p = tmp_path / ".lab.secrets"
    upsert_env(p, "ATLASSIAN_CLIENT_SECRET", "s", secret=True)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600 and p.read_text() == "ATLASSIAN_CLIENT_SECRET=s\n"


def test_registration_body_adds_the_app_callback_when_given():
    b = registration_body("adb-1.2.azuredatabricks.net", app_url="https://agent.example")
    assert b["redirect_uris"][-1] == "https://agent.example/tools-callback" and len(b["redirect_uris"]) == 3
