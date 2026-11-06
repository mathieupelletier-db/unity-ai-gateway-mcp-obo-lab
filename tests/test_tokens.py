import httpx
import jwt
import pytest

from client.tokens import access_token_from, decode_unverified, exchange_for_databricks_token


def test_access_token_from_success():
    assert access_token_from({"access_token": "abc"}) == "abc"


def test_access_token_from_error_includes_description():
    with pytest.raises(RuntimeError, match="AADSTS65001"):
        access_token_from({"error": "invalid_grant", "error_description": "AADSTS65001: consent required"})


def test_exchange_posts_rfc8693_form_without_client_id():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["form"] = dict(httpx.QueryParams(req.content.decode()))
        return httpx.Response(200, json={"access_token": "dbx-token", "expires_in": 3600})

    token = exchange_for_databricks_token("adb-1.2.azuredatabricks.net", "entra-jwt", httpx.Client(transport=httpx.MockTransport(handler)))
    assert token == "dbx-token"
    assert captured["url"] == "https://adb-1.2.azuredatabricks.net/oidc/v1/token"
    assert captured["form"] == {
        "subject_token": "entra-jwt",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "scope": "all-apis",
    }
    assert "client_id" not in captured["form"]  # with client_id the exchange maps to an SP, not the user


def test_exchange_raises_with_body_on_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(400, json={"error": "invalid_request", "error_description": "no matching federation policy"}))
    with pytest.raises(RuntimeError, match="no matching federation policy"):
        exchange_for_databricks_token("ws", "jwt", httpx.Client(transport=transport))


def test_decode_unverified_reads_claims():
    token = jwt.encode({"aud": "x", "email": "a@b.c"}, "k" * 32, algorithm="HS256")
    assert decode_unverified(token)["email"] == "a@b.c"
