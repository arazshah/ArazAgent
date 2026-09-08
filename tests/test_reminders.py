"""Feature 1 (post-Phase-8 roadmap): deadline reminders. due_reminders()
is tested directly against real rows (no LLM, no network) — the polling
loop itself (app.main._reminder_loop) is intentionally untested, same
split as app.review's is_due()/_review_loop.
"""

from __future__ import annotations

from datetime import date, timedelta

from app import reminders
from app.jalali import format_deadline
from app.settings_store import SettingsStore


async def _insert_item(
    pool,
    *,
    item_type: str = "task",
    title: str = "buy milk",
    status: str = "open",
    deadline: date | None = None,
    reminded: bool = False,
) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO items (type, title, decision, status, deadline, reminded_at)
            VALUES (%s, %s, 'auto', %s, %s, CASE WHEN %s THEN now() ELSE NULL END)
            RETURNING id
            """,
            (item_type, title, status, deadline, reminded),
        )
        (item_id,) = await cur.fetchone()
    return item_id


async def test_due_reminders_includes_task_within_lead_time(pool, crypto):
    settings = SettingsStore(pool, crypto)
    tomorrow = date.today() + timedelta(days=1)
    item_id = await _insert_item(pool, deadline=tomorrow)

    due = await reminders.due_reminders(pool, settings)

    assert [row[0] for row in due] == [item_id]


async def test_due_reminders_excludes_deadline_too_far_away(pool, crypto):
    settings = SettingsStore(pool, crypto)
    far_future = date.today() + timedelta(days=10)
    await _insert_item(pool, deadline=far_future)

    due = await reminders.due_reminders(pool, settings)

    assert due == []


async def test_due_reminders_excludes_already_reminded(pool, crypto):
    settings = SettingsStore(pool, crypto)
    tomorrow = date.today() + timedelta(days=1)
    await _insert_item(pool, deadline=tomorrow, reminded=True)

    due = await reminders.due_reminders(pool, settings)

    assert due == []


async def test_due_reminders_excludes_done_tasks(pool, crypto):
    settings = SettingsStore(pool, crypto)
    tomorrow = date.today() + timedelta(days=1)
    await _insert_item(pool, deadline=tomorrow, status="done")

    due = await reminders.due_reminders(pool, settings)

    assert due == []


async def test_due_reminders_excludes_non_task_types(pool, crypto):
    settings = SettingsStore(pool, crypto)
    tomorrow = date.today() + timedelta(days=1)
    await _insert_item(pool, item_type="event", deadline=tomorrow)

    due = await reminders.due_reminders(pool, settings)

    assert due == []


async def test_due_reminders_excludes_tasks_without_deadline(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await _insert_item(pool, deadline=None)

    due = await reminders.due_reminders(pool, settings)

    assert due == []


async def test_due_reminders_disabled_returns_empty(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await settings.set("reminder.enabled", "false")
    tomorrow = date.today() + timedelta(days=1)
    await _insert_item(pool, deadline=tomorrow)

    due = await reminders.due_reminders(pool, settings)

    assert due == []


async def test_due_reminders_large_lead_hours_includes_far_deadline(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await settings.set("reminder.lead_hours", "1000")  # ~41.6 days
    far_future = date.today() + timedelta(days=10)  # well within 1000h lead time
    item_id = await _insert_item(pool, deadline=far_future)

    due = await reminders.due_reminders(pool, settings)

    assert [row[0] for row in due] == [item_id]


async def test_mark_reminded_sets_timestamp_and_excludes_from_next_query(pool, crypto):
    settings = SettingsStore(pool, crypto)
    tomorrow = date.today() + timedelta(days=1)
    item_id = await _insert_item(pool, deadline=tomorrow)

    await reminders.mark_reminded(pool, item_id)

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT reminded_at FROM items WHERE id = %s", (item_id,))
        (reminded_at,) = await cur.fetchone()
    assert reminded_at is not None

    due = await reminders.due_reminders(pool, settings)
    assert due == []


def test_format_reminder_text_includes_id_title_and_jalali_deadline():
    text = reminders.format_reminder_text(5, "buy milk", date(2026, 1, 5))

    assert "#5" in text
    assert "buy milk" in text
    assert format_deadline(date(2026, 1, 5)) in text
