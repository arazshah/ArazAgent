"""End-to-end test through the real /webhook route: HTTP in, row in Postgres,
reply captured — with the outbound Bale HTTP calls mocked via a custom
httpx transport (no live network).
"""

from __future__ import annotations

import json

import httpx
import pytest_asyncio
from cryptography.fernet import Fernet

from app.bootstrap import Bootstrap
from app.main import create_app, lifespan
from tests.fakes import make_text_update


class _FakeBaleTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage"):
            self.sent_messages.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "fake"}})
        return httpx.Response(404, json={"ok": False})


def _boot() -> Bootstrap:
    return Bootstrap(
        DATABASE_URL="postgresql://araz:araz@localhost:5432/araz_test",
        SECRET_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        SESSION_SECRET="y" * 40,
    )


@pytest_asyncio.fixture
async def app_client(pool):
    app = create_app(_boot())
    async with lifespan(app):
        # `pool` fixture points at the same DATABASE_URL and owns truncation
        # cleanup; the app manages its own separate pool object against the
        # same database.
        await app.state.settings.set("bale.webhook_secret", "whsec-test-value")
        await app.state.settings.set("bale.bot_token", "test-token-value")
        await app.state.settings.set("bale.allowed_user_ids", "999")

        fake_transport = _FakeBaleTransport()
        client = await app.state.registry.get_bale_client()
        client._client = httpx.AsyncClient(transport=fake_transport)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            yield http, fake_transport


async def test_webhook_wrong_secret_returns_404(app_client):
    http, _ = app_client
    resp = await http.post("/webhook/wrong-secret", json=make_text_update(1, 999, "hi"))
    assert resp.status_code == 404


async def test_webhook_correct_secret_inserts_row_and_replies(app_client, pool):
    http, fake_transport = app_client
    resp = await http.post("/webhook/whsec-test-value", json=make_text_update(1, 999, "buy milk"))
    assert resp.status_code == 200

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM inbox")
        (count,) = await cur.fetchone()
    assert count == 1
    assert len(fake_transport.sent_messages) == 1


async def test_webhook_redelivery_is_idempotent(app_client, pool):
    http, fake_transport = app_client
    update = make_text_update(7, 999, "same one")
    await http.post("/webhook/whsec-test-value", json=update)
    await http.post("/webhook/whsec-test-value", json=update)

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM inbox")
        (count,) = await cur.fetchone()
    assert count == 1
    assert len(fake_transport.sent_messages) == 1
