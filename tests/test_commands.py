from __future__ import annotations

from app import commands
from app.capture import CaptureContext, handle_update
from app.jalali import format_deadline
from app.settings_store import SettingsStore
from tests.fakes import FakeProvider, make_text_update


def _ctx(pool, crypto):
    settings = SettingsStore(pool, crypto)
    provider = FakeProvider()
    ctx = CaptureContext(
        pool=pool,
        settings=settings,
        provider=provider,
        schedule_background=lambda job: None,
    )
    return ctx, provider


async def test_today_reports_count_and_target(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "one"), ctx)
    await handle_update(make_text_update(2, 999, "two"), ctx)
    await handle_update(make_text_update(3, 999, "/today"), ctx)

    reply = provider.sent[-1][1]
    assert "امروز: 2" in reply
    assert "مجموع: 2 / 100" in reply


async def test_stats_lists_sources_and_statuses(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "one"), ctx)
    await handle_update(make_text_update(2, 999, "/stats"), ctx)

    reply = provider.sent[-1][1]
    assert "bale_text: 1" in reply
    assert "n/a: 1" in reply


async def _insert_item(pool, item_type: str, title: str, deadline: str | None = None) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO items (type, title, decision, deadline) VALUES (%s, %s, 'auto', %s)",
            (item_type, title, deadline),
        )


async def test_items_lists_recent_items_with_type_and_deadline(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")
    await _insert_item(pool, "task", "call the dentist", "2026-01-05")
    await _insert_item(pool, "idea", "weekend trip")

    await handle_update(make_text_update(1, 999, "/items"), ctx)

    reply = provider.sent[-1][1]
    assert "call the dentist" in reply
    assert format_deadline("2026-01-05") in reply
    assert "weekend trip" in reply
    assert "📌" in reply
    assert "💡" in reply


async def test_items_empty_reports_nothing_yet(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "/items"), ctx)

    assert "هنوز" in provider.sent[-1][1]


async def test_tasks_lists_only_open_tasks_ordered_by_deadline(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")
    await _insert_item(pool, "task", "later task", "2026-02-01")
    await _insert_item(pool, "task", "sooner task", "2026-01-01")
    await _insert_item(pool, "idea", "not a task")

    await handle_update(make_text_update(1, 999, "/tasks"), ctx)

    reply = provider.sent[-1][1]
    assert "not a task" not in reply
    assert reply.index("sooner task") < reply.index("later task")


async def test_tasks_excludes_done_tasks(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO items (type, title, decision, status) "
            "VALUES ('task', 'already done', 'auto', 'done')"
        )

    await handle_update(make_text_update(1, 999, "/tasks"), ctx)

    assert "کار بازی وجود ندارد" in provider.sent[-1][1]


async def test_done_marks_task_closed(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO items (type, title, decision) VALUES ('task', 'buy milk', 'auto') "
            "RETURNING id"
        )
        (item_id,) = await cur.fetchone()

    await handle_update(make_text_update(1, 999, f"/done {item_id}"), ctx)

    assert "بسته شد: buy milk" in provider.sent[-1][1]

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT status FROM items WHERE id = %s", (item_id,))
        (status,) = await cur.fetchone()
    assert status == "done"


async def test_done_unknown_id_reports_not_found(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "/done 999999"), ctx)

    assert "پیدا نشد" in provider.sent[-1][1]


async def test_done_without_id_shows_usage(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "/done"), ctx)

    assert "استفاده" in provider.sent[-1][1]


async def test_search_without_query_shows_usage(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "/search"), ctx)

    assert "استفاده" in provider.sent[-1][1]


async def test_search_reports_results_from_search_items(pool, crypto, monkeypatch):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    async def fake_search_items(pool_, settings_, query, limit=5):
        assert query == "milk"
        return [(7, "buy milk", "task", None)]

    monkeypatch.setattr(commands, "search_items", fake_search_items)

    await handle_update(make_text_update(1, 999, "/search milk"), ctx)

    reply = provider.sent[-1][1]
    assert "#7" in reply
    assert "buy milk" in reply


async def test_search_no_results(pool, crypto, monkeypatch):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    async def fake_search_items(pool_, settings_, query, limit=5):
        return []

    monkeypatch.setattr(commands, "search_items", fake_search_items)

    await handle_update(make_text_update(1, 999, "/search nonexistent"), ctx)

    assert "پیدا نشد" in provider.sent[-1][1]


async def test_review_reports_open_tasks(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")
    await _insert_item(pool, "task", "buy milk")

    await handle_update(make_text_update(1, 999, "/review"), ctx)

    reply = provider.sent[-1][1]
    assert "کارهای باز: 1" in reply
    assert "buy milk" in reply
