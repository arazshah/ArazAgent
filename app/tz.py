"""Asia/Tehran calendar-day helpers shared by capture.py and commands.py."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from psycopg_pool import AsyncConnectionPool

TEHRAN = ZoneInfo("Asia/Tehran")


async def count_captured_today(pool: AsyncConnectionPool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT count(*) FROM inbox
            WHERE (captured_at AT TIME ZONE 'Asia/Tehran')::date
                = (now() AT TIME ZONE 'Asia/Tehran')::date
            """
        )
        row = await cur.fetchone()
    assert row is not None
    return row[0]


async def count_total(pool: AsyncConnectionPool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM inbox")
        row = await cur.fetchone()
    assert row is not None
    return row[0]


async def count_by_source(pool: AsyncConnectionPool) -> dict[str, int]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT source, count(*) FROM inbox GROUP BY source")
        rows = await cur.fetchall()
    return {source: count for source, count in rows}


async def count_by_transcript_status(pool: AsyncConnectionPool) -> dict[str, int]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT transcript_status, count(*) FROM inbox GROUP BY transcript_status"
        )
        rows = await cur.fetchall()
    return {status: count for status, count in rows}


async def oldest_unprocessed_age_seconds(pool: AsyncConnectionPool) -> float | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT extract(epoch FROM now() - min(captured_at)) FROM inbox "
            "WHERE processed_at IS NULL"
        )
        row = await cur.fetchone()
    age = row[0] if row is not None else None
    return float(age) if age is not None else None
