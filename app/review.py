"""Phase 5: a periodic summary message — read-only over the items/inbox
data already built by triage (Phase 2) and task tracking (Phase 3). No new
capture, no new LLM calls; this is purely a report.

Sending is driven by app.main._review_loop, which polls is_due() once a
minute. is_due() is kept pure and separate from that loop specifically so
the send-once-a-day logic has direct test coverage without needing to
exercise an infinite loop.
"""

from __future__ import annotations

from datetime import datetime

from psycopg_pool import AsyncConnectionPool

TOP_TASKS_LIMIT = 5
LOOKBACK_DAYS = 7


async def build_review_text(pool: AsyncConnectionPool) -> str:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM items WHERE type = 'task' AND status = 'open'"
        )
        row = await cur.fetchone()
        assert row is not None
        open_tasks = row[0]

        cur = await conn.execute(
            """
            SELECT id, title, deadline FROM items
            WHERE type = 'task' AND status = 'open'
            ORDER BY deadline NULLS LAST, created_at
            LIMIT %s
            """,
            (TOP_TASKS_LIMIT,),
        )
        top_tasks = await cur.fetchall()

        cur = await conn.execute(
            """
            SELECT type, count(*) FROM items
            WHERE created_at > now() - interval '7 days'
            GROUP BY type
            """
        )
        by_type_week = await cur.fetchall()

    lines = ["🗓 مرور دوره‌ای", "", f"کارهای باز: {open_tasks}"]
    for item_id, title, deadline in top_tasks:
        line = f"  #{item_id} {title}"
        if deadline is not None:
            line += f" (تا {deadline.isoformat()})"
        lines.append(line)

    lines.append("")
    lines.append(f"آیتم‌های {LOOKBACK_DAYS} روز اخیر:")
    if by_type_week:
        for item_type, count in sorted(by_type_week):
            lines.append(f"  {item_type}: {count}")
    else:
        lines.append("  چیزی ثبت نشده است.")

    return "\n".join(lines)


def is_due(now: datetime, send_time: str, last_sent_date: str | None) -> bool:
    """True once local time (in `now`'s timezone) has passed send_time
    ("HH:MM") and today's summary hasn't already gone out.
    """
    today = now.date().isoformat()
    if last_sent_date == today:
        return False
    return now.strftime("%H:%M") >= send_time
