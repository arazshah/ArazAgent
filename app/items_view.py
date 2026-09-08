"""Phase 6: read/write helpers behind the admin item browser
(app/admin/routes.py, app/templates/items.html). Plain parameterized SQL,
same style as app/tz.py — no ORM, the queries are simple enough not to
need one.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

VALID_TYPES = ("task", "note", "idea", "event")
VALID_STATUSES = ("open", "done")

PAGE_SIZE = 25
DAILY_LOOKBACK_DAYS = 7


def _where_clause(item_type: str | None, status: str | None) -> tuple[str, list[object]]:
    clauses = []
    params: list[object] = []
    if item_type:
        clauses.append("type = %s")
        params.append(item_type)
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


async def list_items(
    pool: AsyncConnectionPool,
    item_type: str | None,
    status: str | None,
    limit: int = PAGE_SIZE,
    offset: int = 0,
) -> list[tuple]:
    where, params = _where_clause(item_type, status)
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""
            SELECT id, type, title, status, deadline, created_at
            FROM items
            {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        return await cur.fetchall()


async def count_items(pool: AsyncConnectionPool, item_type: str | None, status: str | None) -> int:
    where, params = _where_clause(item_type, status)
    async with pool.connection() as conn:
        cur = await conn.execute(f"SELECT count(*) FROM items {where}", params)
        row = await cur.fetchone()
    assert row is not None
    return row[0]


async def count_by_type(pool: AsyncConnectionPool) -> dict[str, int]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT type, count(*) FROM items GROUP BY type")
        rows = await cur.fetchall()
    return dict(rows)


async def count_by_status(pool: AsyncConnectionPool) -> dict[str, int]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT status, count(*) FROM items GROUP BY status")
        rows = await cur.fetchall()
    return dict(rows)


async def captured_per_day(
    pool: AsyncConnectionPool, days: int = DAILY_LOOKBACK_DAYS
) -> list[tuple[str, int]]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT (captured_at AT TIME ZONE 'Asia/Tehran')::date AS day, count(*)
            FROM inbox
            WHERE captured_at > now() - (%s || ' days')::interval
            GROUP BY day
            ORDER BY day
            """,
            (days,),
        )
        rows = await cur.fetchall()
    return [(day.isoformat(), count) for day, count in rows]


async def set_item_status(pool: AsyncConnectionPool, item_id: int, status: str) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE items SET status = %s, updated_at = now() WHERE id = %s RETURNING id",
            (status, item_id),
        )
        row = await cur.fetchone()
    return row is not None


async def set_item_title(pool: AsyncConnectionPool, item_id: int, title: str) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE items SET title = %s, updated_at = now() WHERE id = %s RETURNING id",
            (title, item_id),
        )
        row = await cur.fetchone()
    return row is not None
