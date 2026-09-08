"""Phase 2 triage: classify_capture() is mocked (no live network) — these
tests cover app.triage's contract: never lose the inbox row, only mark
processed_at once an `items` row genuinely exists (or there is nothing to
classify), and degrade (leave the row unprocessed for recovery) on any
failure.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import triage
from app.settings_store import SettingsStore


@pytest.fixture(autouse=True)
def _no_real_embedding_call(monkeypatch):
    """embed_item() would otherwise make a real network call whenever a test
    configures llm.base_url/api_key, since llm.embedding_model has a default
    value. Tests exercising the embedding hook re-patch this themselves.
    """

    async def noop(pool, settings, item_id, text):
        return None

    monkeypatch.setattr(triage, "embed_item", noop)


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

    async def fake_classify(base_url, api_key, model, text, constitution):
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

    async def fake_classify(base_url, api_key, model, text, constitution):
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
            "schedule",  # no "decision" key in the fake response -> DEFAULT_DECISION
            "زمان‌بندی مشخصی دارد",
        )
    ]
    assert await _processed_at(pool, inbox_id) is not None


async def test_unknown_type_falls_back_to_note(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {"type": "something-unexpected", "title": "x"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    rows = await _items_for(pool, inbox_id)
    assert rows[0][0] == "note"


async def test_garbage_optional_fields_are_dropped_not_fatal(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
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


async def test_successful_classification_triggers_embedding(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool, raw_text="buy milk")

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {"type": "note", "title": "buy milk"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    embed_calls = []

    async def fake_embed_item(pool_, settings_, item_id, text):
        embed_calls.append((item_id, text))

    monkeypatch.setattr(triage, "embed_item", fake_embed_item)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    rows = await _items_for(pool, inbox_id)
    assert len(embed_calls) == 1
    item_id, text = embed_calls[0]
    assert text == "buy milk"
    assert rows[0][1] == "buy milk"  # sanity: same triage run produced the item


async def _score_and_decision(pool, inbox_id: int):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT score, decision, meta FROM items WHERE inbox_id = %s", (inbox_id,)
        )
        return await cur.fetchone()


async def test_classify_capture_receives_constitution_context(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    await settings.set("constitution.goals", "مرجع GeoAI فارسی:5")
    await settings.set("constitution.weekly_capacity_hours", "10")
    inbox_id = await _insert_row(pool)

    received = {}

    async def fake_classify(base_url, api_key, model, text, constitution):
        received.update(constitution)
        return {"type": "note", "title": "x"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    assert received["goals"] == [("مرجع GeoAI فارسی", 5)]
    assert received["weekly_capacity_hours"] == 10


async def test_valid_decision_and_score_are_stored(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {"type": "task", "title": "x", "decision": "do_now", "score": 20}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    score, decision, meta = await _score_and_decision(pool, inbox_id)
    assert score == 20
    assert decision == "do_now"
    assert meta == {}


async def test_invalid_decision_falls_back_to_schedule(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {"type": "task", "title": "x", "decision": "nonsense"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    _score, decision, _meta = await _score_and_decision(pool, inbox_id)
    assert decision == "schedule"


async def test_score_is_clamped_to_valid_range(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {"type": "task", "title": "x", "score": 999}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    score, _decision, _meta = await _score_and_decision(pool, inbox_id)
    assert score == 25


async def test_what_to_drop_instead_stored_in_meta(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {
            "type": "task",
            "title": "x",
            "decision": "do_now",
            "what_to_drop_instead": "کار #12 رو کنار بذار",
        }

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    _score, _decision, meta = await _score_and_decision(pool, inbox_id)
    assert meta == {"what_to_drop_instead": "کار #12 رو کنار بذار"}


async def test_notify_called_with_decision_announcement_on_success(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {
            "type": "task",
            "title": "تماس با دندان‌پزشک",
            "decision": "do_now",
            "score": 20,
            "decision_reason": "فوری است",
            "what_to_drop_instead": "کار #3",
        }

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    announcements = []

    async def notify(message: str) -> None:
        announcements.append(message)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk", notify=notify)

    assert len(announcements) == 1
    text = announcements[0]
    assert "تماس با دندان‌پزشک" in text
    assert "20/25" in text
    assert "فوری است" in text
    assert "کار #3" in text


async def test_notify_not_called_without_a_notify_callback(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {"type": "task", "title": "x"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    # Should simply not attempt to notify anyone — no crash either way.
    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk")

    assert await _items_for(pool, inbox_id) != []


async def test_notify_not_called_on_classification_failure(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {"error": "boom"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    calls = []

    async def notify(message: str) -> None:
        calls.append(message)

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk", notify=notify)

    assert calls == []


async def test_notify_failure_does_not_undo_the_saved_item(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    inbox_id = await _insert_row(pool)

    async def fake_classify(base_url, api_key, model, text, constitution):
        return {"type": "note", "title": "buy milk"}

    monkeypatch.setattr(triage, "classify_capture", fake_classify)

    async def failing_notify(message: str) -> None:
        raise RuntimeError("bot API unreachable")

    await triage.triage_inbox_row(pool, settings, inbox_id, "buy milk", notify=failing_notify)

    assert await _items_for(pool, inbox_id) != []
    assert await _processed_at(pool, inbox_id) is not None
