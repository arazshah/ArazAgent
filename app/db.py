"""Connection pool and schema bootstrap. Plain SQL, no ORM, no Alembic."""

from __future__ import annotations

import logging
from pathlib import Path

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


async def create_pool(database_url: str) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(conninfo=database_url, open=False, min_size=1, max_size=10)
    await pool.open(wait=True, timeout=30)
    return pool


async def apply_schema(pool: AsyncConnectionPool) -> None:
    """Idempotently (re)apply db/schema.sql. Safe to run on every startup."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.connection() as conn:
        await conn.execute(sql)  # type: ignore[arg-type]
    logger.info("schema applied")


async def check_ready(pool: AsyncConnectionPool) -> bool:
    try:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        logger.exception("readiness check failed")
        return False


async def _exec(conn: AsyncConnection, sql: str, params: tuple = ()) -> None:
    await conn.execute(sql, params)
