from __future__ import annotations

from app.capture import CaptureContext, handle_update
from app.settings_store import SettingsStore
from tests.fakes import FakeProvider, make_text_update


def _ctx(pool, crypto):
    settings = SettingsStore(pool, crypto)
    provider = FakeProvider()
    ctx = CaptureContext(
        pool=pool,
        settings=settings,
        provider=provider,
        schedule_background=lambda job: None,
    )
    return ctx, provider


async def test_today_reports_count_and_target(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "one"), ctx)
    await handle_update(make_text_update(2, 999, "two"), ctx)
    await handle_update(make_text_update(3, 999, "/today"), ctx)

    reply = provider.sent[-1][1]
    assert "امروز: 2" in reply
    assert "مجموع: 2 / 100" in reply


async def test_stats_lists_sources_and_statuses(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "one"), ctx)
    await handle_update(make_text_update(2, 999, "/stats"), ctx)

    reply = provider.sent[-1][1]
    assert "bale_text: 1" in reply
    assert "n/a: 1" in reply


async def _insert_item(pool, item_type: str, title: str, deadline: str | None = None) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO items (type, title, decision, deadline) VALUES (%s, %s, 'auto', %s)",
            (item_type, title, deadline),
        )


async def test_items_lists_recent_items_with_type_and_deadline(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")
    await _insert_item(pool, "task", "call the dentist", "2026-01-05")
    await _insert_item(pool, "idea", "weekend trip")

    await handle_update(make_text_update(1, 999, "/items"), ctx)

    reply = provider.sent[-1][1]
    assert "call the dentist" in reply
    assert "2026-01-05" in reply
    assert "weekend trip" in reply
    assert "📌" in reply
    assert "💡" in reply


async def test_items_empty_reports_nothing_yet(pool, crypto):
    ctx, provider = _ctx(pool, crypto)
    await ctx.settings.set("bale.allowed_user_ids", "999")

    await handle_update(make_text_update(1, 999, "/items"), ctx)

    assert "هنوز" in provider.sent[-1][1]
