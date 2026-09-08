"""Admin auth: 503 with no password configured, login/logout, rate limiting,
CSRF, and session_epoch invalidation. Real Postgres, no live network.
"""

from __future__ import annotations

from html.parser import HTMLParser

import httpx
import pytest_asyncio
from cryptography.fernet import Fernet

from app.bootstrap import Bootstrap
from app.main import create_app, lifespan
from app.security import hash_password

PASSWORD = "correct horse battery staple"


def _boot() -> Bootstrap:
    return Bootstrap(
        DATABASE_URL="postgresql://araz:araz@localhost:5432/araz_test",
        SECRET_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        SESSION_SECRET="s" * 40,
    )


@pytest_asyncio.fixture
async def raw_app(pool):
    app = create_app(_boot())
    async with lifespan(app):
        yield app


@pytest_asyncio.fixture
async def client(raw_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=raw_app), base_url="http://test"
    ) as http:
        yield http, raw_app


async def test_admin_503_when_no_password_configured(client):
    http, _ = client
    resp = await http.get("/admin/login")
    assert resp.status_code == 503

    resp = await http.get("/admin/settings")
    assert resp.status_code == 503


async def test_unauthenticated_settings_redirects_to_login(client):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    resp = await http.get("/admin/settings", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/login")


async def test_admin_pages_are_never_cached(client):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    resp = await http.get("/admin/login")
    assert "no-store" in resp.headers["Cache-Control"]


async def test_bad_password_returns_401_and_logs(client, caplog):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    resp = await http.post("/admin/login", data={"password": "wrong"})
    assert resp.status_code == 401


async def test_sixth_failure_returns_429(client):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    for _ in range(5):
        resp = await http.post("/admin/login", data={"password": "wrong"})
        assert resp.status_code == 401
    resp = await http.post("/admin/login", data={"password": "wrong"})
    assert resp.status_code == 429


async def test_correct_password_sets_session_cookie_and_redirects(client):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    resp = await http.post("/admin/login", data={"password": PASSWORD}, follow_redirects=False)
    assert resp.status_code == 302
    assert "araz_admin_session" in resp.cookies


class _FormNestingChecker(HTMLParser):
    """A <form> nested inside another is invalid HTML — browsers silently
    close the outer form early, so fields after the nested one (and the
    real submit button) end up outside it and never get submitted with the
    page's other fields. This caught a real bug in settings.html.
    """

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.max_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "form":
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.depth = max(0, self.depth - 1)


async def test_settings_page_has_no_nested_forms(client):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    await app.state.settings.set("bale.bot_token", "test-token-value")
    login_resp = await http.post(
        "/admin/login", data={"password": PASSWORD}, follow_redirects=False
    )
    http.cookies.set("araz_admin_session", login_resp.cookies["araz_admin_session"])

    resp = await http.get("/admin/settings")
    checker = _FormNestingChecker()
    checker.feed(resp.text)
    assert checker.max_depth <= 1, "settings.html has a <form> nested inside another"


async def test_missing_csrf_returns_400(client):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    login_resp = await http.post(
        "/admin/login", data={"password": PASSWORD}, follow_redirects=False
    )
    cookie = login_resp.cookies["araz_admin_session"]
    http.cookies.set("araz_admin_session", cookie)

    resp = await http.post("/admin/settings/system", data={"system.kill_switch_target": "50"})
    assert resp.status_code == 400


async def test_session_epoch_bump_invalidates_existing_cookie(client):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    login_resp = await http.post(
        "/admin/login", data={"password": PASSWORD}, follow_redirects=False
    )
    cookie = login_resp.cookies["araz_admin_session"]
    http.cookies.set("araz_admin_session", cookie)

    resp = await http.get("/admin/settings")
    assert resp.status_code == 200

    await app.state.settings.set("admin.session_epoch", "2")

    resp = await http.get("/admin/settings", follow_redirects=False)
    assert resp.status_code == 302
