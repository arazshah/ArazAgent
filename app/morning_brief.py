"""Capacity-capped morning brief: unlike app.review's evening summary
(the top 5 open tasks by deadline, plus a weekly count — read the whole
list, no ranking beyond "soonest"), this is a "here's what actually fits
today" list. The gatekeeper isn't only about what to accept at capture
time (see app.constitution, app.triage's capacity guard) — showing more
than fits the day is the same "list only grows" failure in a different
shape, just aimed at the person's morning instead of their plate.

Sending is driven by app.main._morning_brief_loop, on the same
poll-once-a-minute pattern as the evening review — and reuses
app.review.is_due() outright rather than reimplementing the identical
"send once a day, after HH:MM, Tehran time" gate.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.constitution import build_constitution_context
from app.jalali import format_deadline, today_jalali_str
from app.labels import DECISION_LABELS
from app.settings_store import SettingsStore

# Assumed cost for a task with no effort_minutes estimate — without this,
# an un-estimated item would count as free and the capacity cap would
# mean nothing for it.
DEFAULT_ASSUMED_EFFORT_MINUTES = 30

HOURS_PER_WEEK_DAY = 7


async def _capacity_ranked_open_tasks(pool: AsyncConnectionPool) -> list[tuple]:
    """(id, title, deadline, decision, score, effort_minutes, commitment_to),
    overdue/due-today first, then by decision priority (do_now ahead of
    schedule ahead of delegate/archive/decline), then by score — all in
    SQL so the capacity walk below only has to consume it in order.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, title, deadline, decision, score, effort_minutes, commitment_to
            FROM items
            WHERE type = 'task' AND status = 'open'
            ORDER BY
              (deadline IS NOT NULL
                AND deadline <= (now() AT TIME ZONE 'Asia/Tehran')::date) DESC,
              CASE decision
                WHEN 'do_now' THEN 0
                WHEN 'schedule' THEN 1
                WHEN 'delegate' THEN 2
                WHEN 'archive' THEN 3
                WHEN 'decline' THEN 4
                ELSE 5
              END,
              score DESC NULLS LAST,
              id
            """
        )
        return await cur.fetchall()


async def build_morning_brief_text(pool: AsyncConnectionPool, settings: SettingsStore) -> str:
    constitution = await build_constitution_context(pool, settings)
    weekly_capacity_hours = constitution["weekly_capacity_hours"]
    daily_capacity_minutes = max(weekly_capacity_hours, 0) / HOURS_PER_WEEK_DAY * 60

    rows = await _capacity_ranked_open_tasks(pool)

    lines = [f"☀️ خلاصه صبح — {today_jalali_str()}"]
    lines.append(f"ظرفیت امروز: تقریباً {daily_capacity_minutes / 60:.1f} ساعت")
    lines.append("")

    if not rows:
        lines.append("چیزی در صف نیست 🎉")
        return "\n".join(lines)

    included: list[tuple] = []
    total_minutes = 0.0
    for row in rows:
        _id, _title, _deadline, _decision, _score, effort_minutes, _commitment_to = row
        assumed = effort_minutes if effort_minutes is not None else DEFAULT_ASSUMED_EFFORT_MINUTES
        if included and total_minutes + assumed > daily_capacity_minutes:
            break
        included.append(row)
        total_minutes += assumed

    for item_id, title, deadline, decision, score, _effort_minutes, commitment_to in included:
        decision_label = DECISION_LABELS.get(decision, "")
        line = f"#{item_id} {decision_label} {title}"
        if commitment_to:
            line += f" (🤝 {commitment_to})"
        if score is not None:
            line += f" — امتیاز {score}"
        if deadline is not None:
            line += f" (تا {format_deadline(deadline)})"
        lines.append(line)

    remaining = len(rows) - len(included)
    if remaining > 0:
        lines.append(f"\n+{remaining} مورد دیگر در صف، فراتر از ظرفیت امروز — /tasks")

    return "\n".join(lines)
