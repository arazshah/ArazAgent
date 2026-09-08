"""Capacity-capped morning brief: build_morning_brief_text() reads items
directly (no LLM calls) so it's tested against real rows, same pattern as
app.review. The capacity walk (accumulate effort_minutes in priority
order, stop once the daily budget would be exceeded) is the part worth
covering carefully — everything else is formatting.
"""

from __future__ import annotations

from app.morning_brief import DEFAULT_ASSUMED_EFFORT_MINUTES, build_morning_brief_text
from app.settings_store import SettingsStore


async def _insert_task(
    pool,
    title: str,
    decision: str | None = "schedule",
    score: int | None = None,
    effort_minutes: int | None = None,
    deadline: str | None = None,
    commitment_to: str | None = None,
    status: str = "open",
) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO items (type, title, decision, score, effort_minutes,
                                deadline, commitment_to, status)
            VALUES ('task', %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (title, decision, score, effort_minutes, deadline, commitment_to, status),
        )
        (item_id,) = await cur.fetchone()
    return item_id


async def test_empty_when_no_open_tasks(pool, crypto):
    settings = SettingsStore(pool, crypto)

    text = await build_morning_brief_text(pool, settings)

    assert "چیزی در صف نیست" in text


async def test_lists_tasks_that_fit_within_daily_capacity(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await settings.set("constitution.weekly_capacity_hours", "7")  # 1 hour/day
    await _insert_task(pool, "quick task", decision="do_now", score=20, effort_minutes=30)
    await _insert_task(pool, "another quick task", decision="do_now", score=15, effort_minutes=30)

    text = await build_morning_brief_text(pool, settings)

    assert "quick task" in text
    assert "another quick task" in text
    assert "مورد دیگر" not in text  # both fit exactly in 60 minutes


async def test_excludes_tasks_beyond_daily_capacity(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await settings.set("constitution.weekly_capacity_hours", "7")  # 1 hour/day
    await _insert_task(pool, "top priority", decision="do_now", score=20, effort_minutes=45)
    await _insert_task(pool, "too much for today", decision="schedule", score=10, effort_minutes=45)

    text = await build_morning_brief_text(pool, settings)

    assert "top priority" in text
    assert "too much for today" not in text
    assert "+1 مورد دیگر" in text


async def test_always_includes_at_least_the_top_item_even_if_it_exceeds_capacity(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await settings.set("constitution.weekly_capacity_hours", "1")  # ~8.5 min/day
    await _insert_task(pool, "one big task", decision="do_now", score=25, effort_minutes=500)

    text = await build_morning_brief_text(pool, settings)

    assert "one big task" in text


async def test_missing_effort_minutes_uses_assumed_default(pool, crypto):
    settings = SettingsStore(pool, crypto)
    daily_minutes = 60  # weekly_capacity_hours=7 -> 60 min/day
    await settings.set("constitution.weekly_capacity_hours", "7")
    n_fitting = daily_minutes // DEFAULT_ASSUMED_EFFORT_MINUTES
    for i in range(n_fitting + 1):
        await _insert_task(pool, f"unestimated {i}", decision="do_now", score=10)

    text = await build_morning_brief_text(pool, settings)

    assert "+1 مورد دیگر" in text


async def test_orders_overdue_and_due_today_before_everything_else(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await _insert_task(
        pool, "high score but not due", decision="do_now", score=25, effort_minutes=10
    )
    await _insert_task(
        pool, "low score but overdue", decision="schedule", score=1, deadline="2020-01-01"
    )

    text = await build_morning_brief_text(pool, settings)

    assert text.index("low score but overdue") < text.index("high score but not due")


async def test_do_now_ordered_before_schedule_at_equal_score(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await _insert_task(pool, "scheduled item", decision="schedule", score=10, effort_minutes=10)
    await _insert_task(pool, "do it now item", decision="do_now", score=10, effort_minutes=10)

    text = await build_morning_brief_text(pool, settings)

    assert text.index("do it now item") < text.index("scheduled item")


async def test_excludes_done_tasks(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await _insert_task(pool, "already done", status="done")

    text = await build_morning_brief_text(pool, settings)

    assert "already done" not in text


async def test_commitment_marker_shown(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await _insert_task(pool, "send report", decision="do_now", commitment_to="علی")

    text = await build_morning_brief_text(pool, settings)

    assert "🤝 علی" in text
