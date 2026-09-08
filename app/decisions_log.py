"""Calibration loop: `items.decision`/`items.score` are the gatekeeper's
call at capture time, but the user's real actions afterward are the
ground truth. When those two disagree — the model said "decline" or
"archive" and the user closed the item anyway — that disagreement is
exactly the signal that would eventually let scoring/hard-rules be tuned
toward the user's actual judgment (see FUTURE.md; nothing consumes this
log automatically yet, it's just recorded so a future calibration pass
has real data to work from).

`apply_status_change` is the single place `items.status` gets set to
"done" from (the bot's `/done` command and the admin item browser's
toggle both go through it) specifically so this detection can't be
bypassed by adding a third call site later.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

# Decisions where completing the item anyway means the gatekeeper was
# overridden. "do_now"/"schedule"/"delegate" all end in doing the work, so
# completing one of those is just the plan working as decided.
OVERRIDDEN_DECISIONS = ("decline", "archive")


async def apply_status_change(
    pool: AsyncConnectionPool,
    item_id: int,
    new_status: str,
    *,
    only_if_status_differs: bool = False,
) -> tuple[str, str | None, int | None] | None:
    """Sets items.status to new_status, returning (title, decision, score)
    of the updated row — or None if no row matched (unknown item_id, or,
    with only_if_status_differs, the item was already at new_status).

    If this transitions an item to "done" whose decision was "decline" or
    "archive", logs the override to decisions_log in the same statement's
    connection — the write and the log entry share fate.
    """
    guard = " AND status != %s" if only_if_status_differs else ""
    params: list[object] = [new_status, item_id]
    if only_if_status_differs:
        params.append(new_status)

    async with pool.connection() as conn:
        cur = await conn.execute(
            f"UPDATE items SET status = %s, updated_at = now() "
            f"WHERE id = %s{guard} RETURNING title, decision, score",
            params,
        )
        row = await cur.fetchone()
        if row is None:
            return None
        title, decision, score = row
        if new_status == "done" and decision in OVERRIDDEN_DECISIONS:
            await conn.execute(
                "INSERT INTO decisions_log (item_id, decision, score, override_action) "
                "VALUES (%s, %s, %s, %s)",
                (item_id, decision, score, f"completed_despite_{decision}"),
            )
    return title, decision, score


async def count_overrides(pool: AsyncConnectionPool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM decisions_log")
        row = await cur.fetchone()
    assert row is not None
    return row[0]


async def recent_overrides(pool: AsyncConnectionPool, limit: int = 10) -> list[tuple]:
    """(item_id, title, decision, score, override_action, created_at),
    newest first — the raw feed a future calibration review would read.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT decisions_log.item_id, items.title, decisions_log.decision,
                   decisions_log.score, decisions_log.override_action,
                   decisions_log.created_at
            FROM decisions_log
            JOIN items ON items.id = decisions_log.item_id
            ORDER BY decisions_log.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return await cur.fetchall()
