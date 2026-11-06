import asyncio

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_server.auth import EntraJwtValidator
from mcp_server.server import build_app
from tests.helpers import AUD, LAB_BASE_URL, TENANT, FakeJwks


@pytest.fixture(scope="session")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture
def validator(keypair):
    return EntraJwtValidator(TENANT, AUD, jwks_client=FakeJwks(keypair[1]))


@pytest.fixture
async def lab_http(validator):
    """In-process lab MCP server, addressed with an App Service-style Host header.

    The session manager's task group is held in its own task, as uvicorn's lifespan task does in
    production; pytest-asyncio tears async fixtures down in a different task than it set them up.
    """
    app, server = build_app(validator)
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold_session_manager():
        async with server.session_manager.run():
            started.set()
            await stop.wait()

    holder = asyncio.create_task(hold_session_manager())
    await started.wait()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=LAB_BASE_URL) as http:
            yield http
    finally:
        stop.set()
        await holder
