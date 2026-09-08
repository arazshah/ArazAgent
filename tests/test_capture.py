from __future__ import annotations

from app.capture import CaptureContext, handle_update
from app.settings_store import SettingsStore
from tests.fakes import FakeProvider, make_text_update, make_voice_update


def _ctx(pool, crypto, allowed_ids: str = "999") -> tuple[CaptureContext, FakeProvider]:
    settings = SettingsStore(pool, crypto)
    provider = FakeProvider()
    scheduled: list = []
    ctx = CaptureContext(
        pool=pool,
        settings=settings,
        provider=provider,
        schedule_background=lambda job: scheduled.append(job),
    )
    ctx.scheduled = scheduled  # type: ignore[attr-defined]
    return ctx, provider


async def _set_allowed(ctx: CaptureContext, ids: str) -> None:
    await ctx.settings.set("bale.allowed_user_ids", ids)


async def _row_count(pool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM inbox")
        (n,) = await cur.fetchone()
    return n


async def test_text_from_allowed_user_creates_row_and_replies(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    await handle_update(make_text_update(1, user_id=999, text="buy milk"), ctx)

    assert await _row_count(pool) == 1
    assert len(provider.sent) == 1
    chat_id, text = provider.sent[0]
    assert chat_id == 12345
    assert text.startswith("✅ #")

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT source, raw_text, transcript_status FROM inbox")
        row = await cur.fetchone()
    assert row == ("bale_text", "buy milk", "n/a")


async def test_disallowed_user_dropped_silently(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "111")  # msg.user_id below is 999, not allowed

    await handle_update(make_text_update(1, user_id=999, text="buy milk"), ctx)

    assert await _row_count(pool) == 0
    assert provider.sent == []


async def test_empty_allowlist_drops_everything(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    # allowlist never set -> empty

    await handle_update(make_text_update(1, user_id=999, text="buy milk"), ctx)

    assert await _row_count(pool) == 0
    assert provider.sent == []


async def test_duplicate_update_id_creates_one_row_one_reply(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    update = make_text_update(42, user_id=999, text="same message")
    await handle_update(update, ctx)
    await handle_update(update, ctx)  # redelivery

    assert await _row_count(pool) == 1
    assert len(provider.sent) == 1


async def test_malformed_update_never_raises(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    await handle_update({"unexpected": "shape", "message": "not-an-object"}, ctx)

    assert await _row_count(pool) == 0
    assert provider.sent == []


async def test_voice_update_inserts_pending_row_and_schedules_transcription(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    calls = []

    async def fake_transcribe(inbox_id: int, msg, reply_message_id: int | None) -> None:
        calls.append(inbox_id)

    ctx.transcribe_voice = fake_transcribe

    await handle_update(make_voice_update(2, user_id=999), ctx)

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT source, transcript_status FROM inbox")
        row = await cur.fetchone()
    assert row == ("bale_voice", "pending")
    assert len(provider.sent) == 1
    assert "در حال رونویسی" in provider.sent[0][1]
    assert len(ctx.scheduled) == 1  # type: ignore[attr-defined]

    for job in ctx.scheduled:  # type: ignore[attr-defined]
        await job()
    assert calls == [1]


async def test_document_stores_caption_without_transcription(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    update = make_text_update(3, user_id=999, text=None)  # type: ignore[arg-type]
    update["message"]["document"] = {"file_id": "d1", "file_unique_id": "du1"}
    update["message"]["caption"] = "receipt.pdf"
    del update["message"]["text"]

    await handle_update(update, ctx)

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT source, raw_text FROM inbox")
        row = await cur.fetchone()
    assert row == ("bale_document", "receipt.pdf")
    assert "بدون رونویسی" in provider.sent[0][1]


async def test_text_capture_schedules_triage(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    calls = []

    async def fake_triage(inbox_id: int, text: str | None, notify=None) -> None:
        calls.append((inbox_id, text))

    ctx.triage = fake_triage

    await handle_update(make_text_update(6, user_id=999, text="buy milk"), ctx)

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id FROM inbox")
        (inbox_id,) = await cur.fetchone()

    assert len(ctx.scheduled) == 1  # type: ignore[attr-defined]
    for job in ctx.scheduled:  # type: ignore[attr-defined]
        await job()
    assert calls == [(inbox_id, "buy milk")]


async def test_scheduled_triage_notify_callback_sends_via_provider(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    received_notify = []

    async def fake_triage(inbox_id: int, text: str | None, notify=None) -> None:
        received_notify.append(notify)

    ctx.triage = fake_triage

    await handle_update(make_text_update(7, user_id=999, text="buy milk"), ctx)

    for job in ctx.scheduled:  # type: ignore[attr-defined]
        await job()

    assert len(received_notify) == 1
    notify = received_notify[0]
    assert notify is not None

    sent_before = len(provider.sent)
    await notify("🟢 تصمیم: همین حالا انجام بده")

    assert len(provider.sent) == sent_before + 1
    chat_id, text = provider.sent[-1]
    assert chat_id == 12345  # same chat the original message came from
    assert text == "🟢 تصمیم: همین حالا انجام بده"


async def test_slash_command_does_not_create_inbox_row(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    await handle_update(make_text_update(4, user_id=999, text="/start"), ctx)

    assert await _row_count(pool) == 0
    assert len(provider.sent) == 1
    assert "لایه ثبت فعال است" in provider.sent[0][1]


async def test_unknown_command_replies_generic(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await _set_allowed(ctx, "999")

    await handle_update(make_text_update(5, user_id=999, text="/frobnicate"), ctx)

    assert provider.sent[0][1] == "دستور ناشناخته."
