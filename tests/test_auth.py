import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_server.auth import AuthError
from tests.helpers import ISS, TENANT, make_token


def test_valid_token_returns_claims(validator, keypair):
    claims = validator.validate(make_token(keypair[0]))
    assert claims["oid"] == "oid-1"
    assert validator.issuer == ISS


@pytest.mark.parametrize("overrides", [
    {"aud": "someone-else"},
    {"iss": f"https://sts.windows.net/{TENANT}/"},  # Entra v1 issuer must be rejected
    {"exp": int(time.time()) - 60},
    {"scp": "other.scope"},
    {"scp": None},
])
def test_rejects_bad_claims(validator, keypair, overrides):
    with pytest.raises(AuthError):
        validator.validate(make_token(keypair[0], **overrides))


def test_rejects_empty_token(validator):
    with pytest.raises(AuthError):
        validator.validate("")


def test_rejects_token_signed_by_other_key(validator):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        validator.validate(make_token(other))
