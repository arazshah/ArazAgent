"""Phase 5 review: build_review_text() reads items/inbox directly (no LLM
calls, no network) so it's tested against real rows. is_due() is the pure
send-once-a-day gate app.main._review_loop polls every minute — covered
here directly since the loop itself isn't (same as _polling_loop).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.review import build_review_text, is_due

TEHRAN = ZoneInfo("Asia/Tehran")


async def _insert_item(
    pool, item_type: str, title: str, deadline: str | None = None, status: str = "open"
) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO items (type, title, decision, deadline, status) "
            "VALUES (%s, %s, 'auto', %s, %s) RETURNING id",
            (item_type, title, deadline, status),
        )
        (item_id,) = await cur.fetchone()
    return item_id


async def test_review_text_lists_open_tasks_and_weekly_counts(pool, crypto):
    await _insert_item(pool, "task", "call the dentist", "2026-01-05")
    await _insert_item(pool, "task", "closed already", status="done")
    await _insert_item(pool, "idea", "weekend trip")

    text = await build_review_text(pool)

    assert "کارهای باز: 1" in text  # "closed already" is status='done'
    assert "call the dentist" in text
    assert "closed already" not in text
    assert "task: 2" in text  # weekly count includes done tasks too
    assert "idea: 1" in text


async def test_review_text_with_no_items_reports_nothing(pool, crypto):
    text = await build_review_text(pool)

    assert "کارهای باز: 0" in text
    assert "چیزی ثبت نشده است." in text


def test_is_due_false_before_send_time():
    now = datetime(2026, 1, 5, 20, 30, tzinfo=TEHRAN)
    assert is_due(now, "21:00", None) is False


def test_is_due_true_after_send_time_and_not_sent_today():
    now = datetime(2026, 1, 5, 21, 15, tzinfo=TEHRAN)
    assert is_due(now, "21:00", None) is True


def test_is_due_false_if_already_sent_today():
    now = datetime(2026, 1, 5, 22, 0, tzinfo=TEHRAN)
    assert is_due(now, "21:00", "2026-01-05") is False


def test_is_due_true_if_last_sent_was_a_different_day():
    now = datetime(2026, 1, 5, 22, 0, tzinfo=TEHRAN)
    assert is_due(now, "21:00", "2026-01-04") is True
