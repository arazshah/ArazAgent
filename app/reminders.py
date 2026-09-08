"""Feature 1 of the post-Phase-8 roadmap: a once-per-task reminder sent
before an open task's deadline. Read-only over `items` (like
app/review.py) except it stamps `reminded_at`, so a task is reminded
exactly once — never repeated, never lost on a restart (the stamp lives in
Postgres, not in memory).

Sending is driven by app.main._reminder_loop, which polls due_reminders()
once a minute — the same "keep the polling loop thin and untested, test
the pure/DB logic it calls" split used for app.review's _review_loop.
"""

from __future__ import annotations

from datetime import date

from psycopg_pool import AsyncConnectionPool

from app.jalali import format_deadline
from app.settings_store import SettingsStore

DEFAULT_LEAD_HOURS = 24


async def due_reminders(
    pool: AsyncConnectionPool, settings: SettingsStore
) -> list[tuple[int, str, date]]:
    """Returns [(id, title, deadline), ...] for open tasks whose reminder
    lead time has arrived and that haven't been reminded yet. Empty list
    if reminders are disabled or nothing qualifies.
    """
    enabled = (await settings.get("reminder.enabled")) or "true"
    if enabled.strip().lower() == "false":
        return []

    try:
        lead_hours = int((await settings.get("reminder.lead_hours")) or str(DEFAULT_LEAD_HOURS))
    except ValueError:
        lead_hours = DEFAULT_LEAD_HOURS

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, title, deadline FROM items
            WHERE type = 'task' AND status = 'open'
              AND deadline IS NOT NULL
              AND reminded_at IS NULL
              AND (deadline::timestamp - (%s || ' hours')::interval)
                  <= (now() AT TIME ZONE 'Asia/Tehran')
            ORDER BY deadline
            """,
            (lead_hours,),
        )
        return await cur.fetchall()


async def mark_reminded(pool: AsyncConnectionPool, item_id: int) -> None:
    async with pool.connection() as conn:
        await conn.execute("UPDATE items SET reminded_at = now() WHERE id = %s", (item_id,))


def format_reminder_text(item_id: int, title: str, deadline: date) -> str:
    return f"⏰ یادآوری: #{item_id} {title} — سررسید {format_deadline(deadline)}"
