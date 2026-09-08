from __future__ import annotations

import json
from types import SimpleNamespace

from app.recovery import recover_stuck_transcriptions, recover_stuck_triage
from app.settings_store import SettingsStore
from tests.fakes import FakeProvider, make_voice_update


class _FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider

    async def get_bale_client(self) -> FakeProvider:
        return self._provider


async def _insert_stuck_row(pool, msg, minutes_old: int = 15) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO inbox (source, transcript_status, provider, provider_update_id,
                                provider_message_id, provider_chat_id, raw_update, captured_at)
            VALUES ('bale_voice', 'pending', 'bale', %s, %s, %s, %s,
                    now() - (%s || ' minutes')::interval)
            RETURNING id
            """,
            (msg.update_id, msg.message_id, msg.chat_id, json.dumps(msg.raw), minutes_old),
        )
        (inbox_id,) = await cur.fetchone()
    return inbox_id


def _fake_app(pool, crypto, provider):
    settings = SettingsStore(pool, crypto)
    calls: list[int] = []

    async def transcribe_voice(inbox_id, msg, reply_message_id):
        calls.append(inbox_id)

    state = SimpleNamespace(
        pool=pool,
        settings=settings,
        registry=_FakeRegistry(provider),
        transcribe_voice=transcribe_voice,
    )
    return SimpleNamespace(state=state), calls


async def test_recovers_rows_stuck_over_ten_minutes(pool, crypto):
    provider = FakeProvider()
    app, calls = _fake_app(pool, crypto, provider)
    msg = provider.parse_update(make_voice_update(1, 999))
    inbox_id = await _insert_stuck_row(pool, msg, minutes_old=15)

    recovered = await recover_stuck_transcriptions(app)

    assert recovered == 1
    assert calls == [inbox_id]


async def test_does_not_recover_recent_pending_rows(pool, crypto):
    provider = FakeProvider()
    app, calls = _fake_app(pool, crypto, provider)
    msg = provider.parse_update(make_voice_update(2, 999))
    await _insert_stuck_row(pool, msg, minutes_old=2)

    recovered = await recover_stuck_transcriptions(app)

    assert recovered == 0
    assert calls == []


async def test_no_transcribe_backend_configured_recovers_nothing(pool, crypto):
    provider = FakeProvider()
    settings = SettingsStore(pool, crypto)
    msg = provider.parse_update(make_voice_update(3, 999))
    await _insert_stuck_row(pool, msg, minutes_old=15)

    app = SimpleNamespace(
        state=SimpleNamespace(
            pool=pool, settings=settings, registry=_FakeRegistry(provider), transcribe_voice=None
        )
    )
    recovered = await recover_stuck_transcriptions(app)
    assert recovered == 0


async def _insert_untriaged_row(
    pool, source: str = "bale_text", transcript_status: str = "n/a", minutes_old: int = 15
) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO inbox (source, raw_text, transcript_status, provider, captured_at)
            VALUES (%s, 'buy milk', %s, 'bale', now() - (%s || ' minutes')::interval)
            RETURNING id
            """,
            (source, transcript_status, minutes_old),
        )
        (inbox_id,) = await cur.fetchone()
    return inbox_id


def _fake_app_with_triage(pool):
    calls: list[tuple[int, str | None]] = []

    async def triage(inbox_id, text):
        calls.append((inbox_id, text))

    return SimpleNamespace(state=SimpleNamespace(pool=pool, triage=triage)), calls


async def test_recovers_untriaged_rows_stuck_over_ten_minutes(pool, crypto):
    app, calls = _fake_app_with_triage(pool)
    inbox_id = await _insert_untriaged_row(pool, minutes_old=15)

    triaged = await recover_stuck_triage(app)

    assert triaged == 1
    assert calls == [(inbox_id, "buy milk")]


async def test_does_not_recover_recent_untriaged_rows(pool, crypto):
    app, calls = _fake_app_with_triage(pool)
    await _insert_untriaged_row(pool, minutes_old=2)

    triaged = await recover_stuck_triage(app)

    assert triaged == 0
    assert calls == []


async def test_does_not_recover_rows_still_pending_transcription(pool, crypto):
    app, calls = _fake_app_with_triage(pool)
    await _insert_untriaged_row(
        pool, source="bale_voice", transcript_status="pending", minutes_old=15
    )

    triaged = await recover_stuck_triage(app)

    assert triaged == 0
    assert calls == []


async def test_no_triage_handler_configured_recovers_nothing(pool, crypto):
    await _insert_untriaged_row(pool, minutes_old=15)
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, triage=None))

    triaged = await recover_stuck_triage(app)

    assert triaged == 0
