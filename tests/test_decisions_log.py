"""Calibration loop (app/decisions_log.py): completing an item the
gatekeeper had decided to "decline" or "archive" is an override worth
recording; completing anything else is just the plan working.
"""

from __future__ import annotations

from app import decisions_log


async def _insert_item(pool, decision: str | None = "auto", score: int | None = None) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO items (type, title, decision, score) "
            "VALUES ('task', 'buy milk', %s, %s) RETURNING id",
            (decision, score),
        )
        (item_id,) = await cur.fetchone()
    return item_id


async def _log_rows(pool, item_id: int) -> list[tuple]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT decision, score, override_action FROM decisions_log WHERE item_id = %s",
            (item_id,),
        )
        return await cur.fetchall()


async def test_completing_a_declined_item_logs_an_override(pool, crypto):
    item_id = await _insert_item(pool, decision="decline", score=3)

    result = await decisions_log.apply_status_change(pool, item_id, "done")

    assert result == ("buy milk", "decline", 3)
    assert await _log_rows(pool, item_id) == [("decline", 3, "completed_despite_decline")]
    assert await decisions_log.count_overrides(pool) == 1


async def test_completing_an_archived_item_logs_an_override(pool, crypto):
    item_id = await _insert_item(pool, decision="archive", score=1)

    await decisions_log.apply_status_change(pool, item_id, "done")

    assert await _log_rows(pool, item_id) == [("archive", 1, "completed_despite_archive")]


async def test_completing_a_do_now_item_logs_nothing(pool, crypto):
    item_id = await _insert_item(pool, decision="do_now", score=20)

    await decisions_log.apply_status_change(pool, item_id, "done")

    assert await _log_rows(pool, item_id) == []
    assert await decisions_log.count_overrides(pool) == 0


async def test_reopening_a_declined_item_logs_nothing(pool, crypto):
    """Only completing (-> "done") a declined/archived item is an override
    — reopening one that was previously closed isn't the same signal.
    """
    item_id = await _insert_item(pool, decision="decline")

    await decisions_log.apply_status_change(pool, item_id, "done")
    await decisions_log.apply_status_change(pool, item_id, "open")

    assert await decisions_log.count_overrides(pool) == 1  # only the "done" transition


async def test_unknown_item_id_returns_none_and_logs_nothing(pool, crypto):
    result = await decisions_log.apply_status_change(pool, 999999, "done")

    assert result is None
    assert await decisions_log.count_overrides(pool) == 0


async def test_only_if_status_differs_skips_already_matching_status(pool, crypto):
    item_id = await _insert_item(pool, decision="decline")
    async with pool.connection() as conn:
        await conn.execute("UPDATE items SET status = 'done' WHERE id = %s", (item_id,))

    result = await decisions_log.apply_status_change(
        pool, item_id, "done", only_if_status_differs=True
    )

    assert result is None
    assert await decisions_log.count_overrides(pool) == 0


async def test_recent_overrides_returns_newest_first_with_item_title(pool, crypto):
    first = await _insert_item(pool, decision="decline", score=2)
    await decisions_log.apply_status_change(pool, first, "done")
    second = await _insert_item(pool, decision="archive", score=4)
    await decisions_log.apply_status_change(pool, second, "done")

    rows = await decisions_log.recent_overrides(pool)

    assert [r[0] for r in rows] == [second, first]
    assert rows[0][1] == "buy milk"  # title joined from items
    assert rows[0][2] == "archive"


async def test_recent_overrides_respects_limit(pool, crypto):
    for _ in range(3):
        item_id = await _insert_item(pool, decision="decline")
        await decisions_log.apply_status_change(pool, item_id, "done")

    rows = await decisions_log.recent_overrides(pool, limit=2)

    assert len(rows) == 2
