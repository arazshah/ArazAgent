"""Phase 2 triage: classify_capture() is mocked (no live network) — these
tests cover app.triage's contract: never lose the inbox row, only mark
processed_at once an `items` row genuinely exists (or there is nothing to
classify), and degrade (leave the row unprocessed for recovery) on any
failure.
"""

from __future__ import annotations

from datetime import date

from app import triage
from app.settings_store import SettingsStore


async def _insert_row(pool, raw_text: str | None = "buy milk") -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO inbox (source, raw_text, transcript_status, provider) "
            "VALUES ('bale_text', %s, 'n/a', 'bale') RETURNING id",
            (raw_text,),
        )
        (inbox_id,) = await cur.fetchone()
    return inbox_id


async def _processed_at(pool, inbox_id: int):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT processed_at FROM inbox WHERE id = %s", (inbox_id,))
        (value,) = await cur.fetchone()
    return value


async def _items_for(pool, inbox_id: int) -> list[tuple]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT type, title, project, goal_key, effort_minutes, deadline, "
            "decision, decision_reason FROM items WHERE inbox_id = %s",
            (inbox_id,),
        )
        return await cur.fetchall()


async def _configure_llm(settings: SettingsStore) -> None:
    await settings.set("llm.base_url", "https://api.avalai.ir/v1")
    await settings.set("llm.api_key", "test-key")
    await settings.set("llm.chat_model", "gpt-4o-mini")


async def test_disabled_triage_leaves_row_unprocessed(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await settings.set("llm.triage_enabled", "false")
    inbox_id = await _insert_row(pool)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    assert await _items_for(pool, inbox_id) == []
    assert await _processed_at(pool, inbox_id) is None


async def test_empty_text_marks_processed_without_item(pool, crypto):
    settings = SettingsStore(pool, crypto)
    inbox_id = await _insert_row(pool, raw_text=None)

    await triage.triage_inbox_row(pool, settings, inbox_id, None)

    assert await _items_for(pool, inbox_id) == []
    assert await _processed_at(pool, inbox_id) is not None


async def test_llm_not_configured_leaves_row_unprocessed(pool, crypto):
    settings = SettingsStore(pool, crypto)
    inbox_id = await _insert_row(pool)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    assert await _items_for(pool, inbox_id) == []
    assert await _processed_at(pool, inbox_id) is None


async def test_classification_error_leaves_row_unprocessed(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text):
        return {"error": "upstream exploded"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    assert await _items_for(pool, inbox_id) == []
    assert await _processed_at(pool, inbox_id) is None


async def test_successful_classification_inserts_item_and_marks_processed(
    pool, crypto, monkeypatch
):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool, raw_text="call the dentist by 2026-01-05, ~15 min")

    async def fake_classify(base_url, api_key, model, text):
        return {
            "type": "task",
            "title": "تماس با دندان‌پزشک",
            "project": "سلامت",
            "goal_key": "health",
            "effort_minutes": 15,
            "deadline": "2026-01-05",
            "decision_reason": "زمان‌بندی مشخصی دارد",
        }

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "call the dentist by 2026-01-05")

    rows = await _items_for(pool, inbox_id)
    assert rows == [
        (
            "task",
            "تماس با دندان‌پزشک",
            "سلامت",
            "health",
            15,
            date(2026, 1, 5),
            "auto",
            "زمان‌بندی مشخصی دارد",
        )
    ]
    assert await _processed_at(pool, inbox_id) is not None


async def test_unknown_type_falls_back_to_note(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text):
        return {"type": "something-unexpected", "title": "x"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    rows = await _items_for(pool, inbox_id)
    assert rows[0][0] == "note"


async def test_garbage_optional_fields_are_dropped_not_fatal(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text):
        return {
            "type": "note",
            "title": "",  # falls back to the raw text
            "effort_minutes": "not-a-number",
            "deadline": "not-a-date",
            "project": "   ",
        }

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    rows = await _items_for(pool, inbox_id)
    item_type, title, project, goal_key, effort_minutes, deadline, decision, reason = rows[0]
    assert item_type == "note"
    assert title == "buy milk"
    assert project is None
    assert effort_minutes is None
    assert deadline is None
