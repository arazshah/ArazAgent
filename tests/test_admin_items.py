"""Admin item browser (Phase 6): auth-gated, CSRF-protected toggle/edit,
and filters. Real Postgres, no live network — same client pattern as
tests/test_admin_auth.py.
"""

from __future__ import annotations

import re
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


async def _login(http, app) -> None:
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    resp = await http.post("/admin/login", data={"password": PASSWORD}, follow_redirects=False)
    http.cookies.set("araz_admin_session", resp.cookies["araz_admin_session"])


async def _insert_item(pool, item_type: str = "task", title: str = "buy milk") -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO items (type, title, decision) VALUES (%s, %s, 'auto') RETURNING id",
            (item_type, title),
        )
        (item_id,) = await cur.fetchone()
    return item_id


async def _csrf_token(resp_text: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', resp_text)
    assert match is not None
    return match.group(1)


async def test_items_page_requires_login(client):
    http, app = client
    await app.state.settings.set("admin.password_hash", hash_password(PASSWORD))
    resp = await http.get("/admin/items", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("/login")


async def test_items_page_lists_items(client, pool):
    http, app = client
    await _login(http, app)
    await _insert_item(pool, "task", "buy milk")

    resp = await http.get("/admin/items")
    assert resp.status_code == 200
    assert "buy milk" in resp.text


async def test_items_page_filters_by_type(client, pool):
    http, app = client
    await _login(http, app)
    await _insert_item(pool, "task", "a task")
    await _insert_item(pool, "idea", "an idea")

    resp = await http.get("/admin/items", params={"type": "task"})
    assert "a task" in resp.text
    assert "an idea" not in resp.text


async def test_items_page_shows_commitment_badge(client, pool):
    http, app = client
    await _login(http, app)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO items (type, title, decision, commitment_to) "
            "VALUES ('task', 'send the report', 'do_now', 'علی')"
        )

    resp = await http.get("/admin/items")

    assert "تعهد به علی" in resp.text


async def test_items_page_filters_by_commitments_only(client, pool):
    http, app = client
    await _login(http, app)
    await _insert_item(pool, "task", "personal task")
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO items (type, title, decision, commitment_to) "
            "VALUES ('task', 'a promise', 'do_now', 'مریم')"
        )

    resp = await http.get("/admin/items", params={"commitments": "1"})

    assert "a promise" in resp.text
    assert "personal task" not in resp.text


async def test_toggle_requires_csrf(client, pool):
    http, app = client
    await _login(http, app)
    item_id = await _insert_item(pool)

    resp = await http.post(f"/admin/items/{item_id}/toggle", data={"new_status": "done"})
    assert resp.status_code == 400


async def test_toggle_closes_and_reopens_item(client, pool):
    http, app = client
    await _login(http, app)
    item_id = await _insert_item(pool)

    page = await http.get("/admin/items")
    csrf = await _csrf_token(page.text)

    resp = await http.post(
        f"/admin/items/{item_id}/toggle",
        data={"csrf_token": csrf, "new_status": "done"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT status FROM items WHERE id = %s", (item_id,))
        (status,) = await cur.fetchone()
    assert status == "done"


async def test_edit_updates_title(client, pool):
    http, app = client
    await _login(http, app)
    item_id = await _insert_item(pool, title="old title")

    page = await http.get("/admin/items")
    csrf = await _csrf_token(page.text)

    resp = await http.post(
        f"/admin/items/{item_id}/edit",
        data={"csrf_token": csrf, "title": "new title"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT title FROM items WHERE id = %s", (item_id,))
        (title,) = await cur.fetchone()
    assert title == "new title"


class _FormNestingChecker(HTMLParser):
    """Same regression check as tests/test_admin_auth.py's — a <form> nested
    inside another silently drops the fields after it in real browsers.
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


async def test_items_page_has_no_nested_forms(client, pool):
    http, app = client
    await _login(http, app)
    await _insert_item(pool)

    resp = await http.get("/admin/items")
    checker = _FormNestingChecker()
    checker.feed(resp.text)
    assert checker.max_depth <= 1, "items.html has a <form> nested inside another"


async def test_edit_rejects_empty_title(client, pool):
    http, app = client
    await _login(http, app)
    item_id = await _insert_item(pool)

    page = await http.get("/admin/items")
    csrf = await _csrf_token(page.text)

    resp = await http.post(
        f"/admin/items/{item_id}/edit",
        data={"csrf_token": csrf, "title": "   "},
    )
    assert resp.status_code == 400
