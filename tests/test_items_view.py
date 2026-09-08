from __future__ import annotations

from app import items_view


async def _insert_item(
    pool, item_type: str = "task", title: str = "buy milk", status: str = "open"
) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO items (type, title, decision, status) "
            "VALUES (%s, %s, 'auto', %s) RETURNING id",
            (item_type, title, status),
        )
        (item_id,) = await cur.fetchone()
    return item_id


async def test_list_items_filters_by_type_and_status(pool, crypto):
    await _insert_item(pool, "task", "open task")
    await _insert_item(pool, "task", "done task", status="done")
    await _insert_item(pool, "idea", "an idea")

    rows = await items_view.list_items(pool, "task", "open")

    assert [r[2] for r in rows] == ["open task"]


async def test_list_items_no_filter_returns_everything(pool, crypto):
    await _insert_item(pool, "task", "a")
    await _insert_item(pool, "idea", "b")

    rows = await items_view.list_items(pool, None, None)

    assert len(rows) == 2


async def test_count_items_matches_filter(pool, crypto):
    await _insert_item(pool, "task", "a")
    await _insert_item(pool, "idea", "b")

    assert await items_view.count_items(pool, "task", None) == 1
    assert await items_view.count_items(pool, None, None) == 2


async def test_count_by_type_and_status(pool, crypto):
    await _insert_item(pool, "task", "a")
    await _insert_item(pool, "task", "b", status="done")
    await _insert_item(pool, "idea", "c")

    assert await items_view.count_by_type(pool) == {"task": 2, "idea": 1}
    assert await items_view.count_by_status(pool) == {"open": 2, "done": 1}


async def test_set_item_status_updates_and_reports_success(pool, crypto):
    item_id = await _insert_item(pool)

    assert await items_view.set_item_status(pool, item_id, "done") is True

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT status FROM items WHERE id = %s", (item_id,))
        (status,) = await cur.fetchone()
    assert status == "done"


async def test_set_item_status_unknown_id_returns_false(pool, crypto):
    assert await items_view.set_item_status(pool, 999999, "done") is False


async def test_set_item_title_updates_and_reports_success(pool, crypto):
    item_id = await _insert_item(pool, title="old title")

    assert await items_view.set_item_title(pool, item_id, "new title") is True

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT title FROM items WHERE id = %s", (item_id,))
        (title,) = await cur.fetchone()
    assert title == "new title"


async def test_captured_per_day_counts_recent_inbox_rows(pool, crypto):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO inbox (source, raw_text, transcript_status, provider) "
            "VALUES ('bale_text', 'hi', 'n/a', 'bale')"
        )

    daily = await items_view.captured_per_day(pool)

    assert sum(count for _, count in daily) == 1
