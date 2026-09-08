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
