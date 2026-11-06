import pytest
from infra.lab_payloads import APP_ID_URI_HOST, app_id_uri, connection_payload, mcp_service_payload

ENV = {
    "TENANT_ID": "11111111-1111-1111-1111-111111111111",
    "MCP_API_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
    "MCP_SERVER_URL": "https://mcp-lab-mcp-1234.azurewebsites.net/mcp",
    "UC_CONN_CLIENT_ID": "c0ffee00-1111-2222-3333-444455556666",
}
SECRETS = {"UC_CONN_CLIENT_SECRET": "s3cret"}


def test_app_id_uri_is_https_on_verified_domain():
    assert app_id_uri() == f"https://{APP_ID_URI_HOST}/mcp-lab-mcp-api"
    assert app_id_uri().startswith("https://")  # mcp_resource rejects api://


def test_connection_payload_is_schema_level_u2m_per_user():
    p = connection_payload(ENV, SECRETS)
    assert p["name"] == "mcp_lab_mcp_connection"
    assert p["parent"] == "schemas/mcp_obo_lab.mcp"
    assert p["connection_type"] == "HTTP"
    assert p["credential_type"] == "OAUTH_U2M_MAPPING"
    o = p["options"]
    assert o["host"] == "https://mcp-lab-mcp-1234.azurewebsites.net"
    assert o["base_path"] == "/mcp"
    assert o["client_id"] == ENV["UC_CONN_CLIENT_ID"]
    assert o["client_secret"] == "s3cret"
    assert o["oauth_credential_exchange_method"] == "body_only"
    assert o["authorization_endpoint"] == f"https://login.microsoftonline.com/{ENV['TENANT_ID']}/oauth2/v2.0/authorize"
    assert o["token_endpoint"] == f"https://login.microsoftonline.com/{ENV['TENANT_ID']}/oauth2/v2.0/token"


def test_resource_and_scope_share_the_app_id_uri():
    # AADSTS9010010 guard: the RFC 8707 resource sent by Databricks must match the scope's resource
    o = connection_payload(ENV, SECRETS)["options"]
    assert o["mcp_resource"] == app_id_uri()
    assert o["oauth_scope"] == f"{app_id_uri()}/mcp.invoke offline_access"


def test_mcp_service_payload_selects_tools_and_rate_limits():
    p = mcp_service_payload()
    c = p["config"]
    assert c["source_connection"] == {"name": "connections/mcp_obo_lab.mcp.mcp_lab_mcp_connection"}
    assert c["include_tool_selectors"] == ["whoami", "echo"]
    assert {(r["key"], r["requests"]) for r in c["rate_limits"]} == {("RATE_LIMIT_KEY_SERVICE", 60), ("RATE_LIMIT_KEY_USER_DEFAULT", 10)}
    assert all(r["renewal_period"] == "RATE_LIMIT_RENEWAL_PERIOD_MINUTE" for r in c["rate_limits"])


def test_connection_client_must_not_be_the_mcp_api_app():
    # AADSTS90009 guard: Entra rejects refresh when the app requests a token for itself via an https resource
    import pytest
    with pytest.raises(ValueError, match="separate app registration"):
        connection_payload({**ENV, "UC_CONN_CLIENT_ID": ENV["MCP_API_CLIENT_ID"]}, SECRETS)


from infra.lab_payloads import (ATLASSIAN_RESOURCE, ATLASSIAN_SCOPES, atlassian_connection_payload,  # noqa: E402
                                atlassian_mcp_service_payload)

A_ENV = {"AGW_FQDN": "mcp-lab-agw.canadacentral.cloudapp.azure.com", "ATLASSIAN_CLIENT_ID": "atl-client-1"}
A_SECRETS = {"ATLASSIAN_CLIENT_SECRET": "atl-s3cret"}


def test_atlassian_connection_routes_via_app_gateway():
    p = atlassian_connection_payload(A_ENV, A_SECRETS)
    assert p["name"] == "mcp_lab_atlassian_conn" and p["parent"] == "schemas/mcp_obo_lab.mcp"
    assert p["connection_type"] == "HTTP" and p["credential_type"] == "OAUTH_U2M_MAPPING"
    o = p["options"]
    assert o["host"] == "https://mcp-lab-agw.canadacentral.cloudapp.azure.com" and o["base_path"] == "/v2/mcp"
    assert o["mcp_resource"] == ATLASSIAN_RESOURCE == "https://mcp.atlassian.com/v2/mcp"
    assert o["authorization_endpoint"] == "https://auth.atlassian.com/authorize"
    assert o["token_endpoint"] == "https://auth.atlassian.com/oauth/token"
    assert o["client_id"] == "atl-client-1" and o["client_secret"] == "atl-s3cret"
    assert o["oauth_scope"] == ATLASSIAN_SCOPES and "offline_access" in o["oauth_scope"].split()
    assert not any(s.startswith("write:") for s in o["oauth_scope"].split())
    assert o["oauth_credential_exchange_method"] == "body_only"


def test_atlassian_connection_refuses_direct_atlassian_host():
    # Review Focus 1: a host of mcp.atlassian.com would bypass the App Gateway this lab exists to prove
    with pytest.raises(ValueError):
        atlassian_connection_payload({**A_ENV, "AGW_FQDN": "mcp.atlassian.com"}, A_SECRETS)


def test_atlassian_service_payload_selectors_optional():
    bare = atlassian_mcp_service_payload()
    assert "include_tool_selectors" not in bare["config"]
    assert bare["config"]["source_connection"] == {"name": "connections/mcp_obo_lab.mcp.mcp_lab_atlassian_conn"}
    assert bare["config"]["rate_limits"] == mcp_service_payload()["config"]["rate_limits"]
    assert atlassian_mcp_service_payload(["a", "b"])["config"]["include_tool_selectors"] == ["a", "b"]
