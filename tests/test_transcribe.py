"""Voice transcription orchestration: success updates the row to 'done' and
edits/replies with the transcript; failure marks 'failed' but keeps the row
and the downloaded audio — never lose a capture.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.settings_store import SettingsStore
from app.transcribe import orchestrator
from tests.fakes import FakeProvider, make_voice_update


class _FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider

    async def get_bale_client(self) -> FakeProvider:
        return self._provider


def _fake_app(pool, crypto, audio_dir, provider: FakeProvider):
    settings = SettingsStore(pool, crypto)
    boot = SimpleNamespace(audio_dir=str(audio_dir))
    state = SimpleNamespace(
        pool=pool, settings=settings, bootstrap=boot, registry=_FakeRegistry(provider)
    )
    return SimpleNamespace(state=state), settings


async def _insert_pending_row(pool, msg) -> int:
    import json

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO inbox (source, transcript_status, provider, provider_update_id,
                                provider_message_id, provider_chat_id, raw_update)
            VALUES ('bale_voice', 'pending', 'bale', %s, %s, %s, %s)
            RETURNING id
            """,
            (msg.update_id, msg.message_id, msg.chat_id, json.dumps(msg.raw)),
        )
        (inbox_id,) = await cur.fetchone()
    return inbox_id


async def test_successful_transcription_marks_done_and_replies(pool, crypto, tmp_path, monkeypatch):
    provider = FakeProvider()
    app, settings = _fake_app(pool, crypto, tmp_path, provider)
    await settings.set("transcription.backend", "avalai")
    await settings.set("transcription.language", "fa")

    update = make_voice_update(1, 999)
    msg = provider.parse_update(update)
    inbox_id = await _insert_pending_row(pool, msg)

    class _StubBackend:
        async def transcribe(self, audio_bytes: bytes, language: str) -> str:
            return "buy milk tomorrow"

    async def fake_build_backend(app):
        return _StubBackend(), "avalai"

    monkeypatch.setattr(orchestrator, "build_backend", fake_build_backend)

    await orchestrator.transcribe_voice_job(app, inbox_id, msg, reply_message_id=7)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT transcript_status, raw_text, transcript_backend, audio_path "
            "FROM inbox WHERE id = %s",
            (inbox_id,),
        )
        status, raw_text, backend, audio_path = await cur.fetchone()

    assert status == "done"
    assert raw_text == "buy milk tomorrow"
    assert backend == "avalai"
    assert audio_path is not None
    assert provider.edits == [(12345, 7, f"#{inbox_id}: buy milk tomorrow")]


async def test_successful_transcription_triggers_triage(pool, crypto, tmp_path, monkeypatch):
    """processed_at is Phase 2's to set, not transcription's — a completed
    transcript still needs triage before the row counts as handled.
    """
    provider = FakeProvider()
    app, settings = _fake_app(pool, crypto, tmp_path, provider)
    await settings.set("transcription.backend", "avalai")

    update = make_voice_update(9, 999)
    msg = provider.parse_update(update)
    inbox_id = await _insert_pending_row(pool, msg)

    class _StubBackend:
        async def transcribe(self, audio_bytes: bytes, language: str) -> str:
            return "call the dentist tomorrow"

    async def fake_build_backend(app):
        return _StubBackend(), "avalai"

    monkeypatch.setattr(orchestrator, "build_backend", fake_build_backend)

    triaged = []

    async def fake_triage(pool_, settings_, inbox_id_, text):
        triaged.append((inbox_id_, text))

    monkeypatch.setattr(orchestrator, "triage_inbox_row", fake_triage)

    await orchestrator.transcribe_voice_job(app, inbox_id, msg, reply_message_id=None)

    assert triaged == [(inbox_id, "call the dentist tomorrow")]

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT processed_at FROM inbox WHERE id = %s", (inbox_id,))
        (processed_at,) = await cur.fetchone()
    assert processed_at is None  # triage (mocked here) is what would set it


async def test_transcription_failure_marks_failed_and_keeps_audio(
    pool, crypto, tmp_path, monkeypatch
):
    provider = FakeProvider()
    app, settings = _fake_app(pool, crypto, tmp_path, provider)
    await settings.set("transcription.backend", "avalai")

    update = make_voice_update(2, 999)
    msg = provider.parse_update(update)
    inbox_id = await _insert_pending_row(pool, msg)

    class _FailingBackend:
        async def transcribe(self, audio_bytes: bytes, language: str) -> str:
            raise RuntimeError("upstream exploded")

    async def fake_build_backend(app):
        return _FailingBackend(), "avalai"

    monkeypatch.setattr(orchestrator, "build_backend", fake_build_backend)

    await orchestrator.transcribe_voice_job(app, inbox_id, msg, reply_message_id=7)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT transcript_status, transcript_error, audio_path FROM inbox WHERE id = %s",
            (inbox_id,),
        )
        status, error, audio_path = await cur.fetchone()

    assert status == "failed"
    assert "upstream exploded" in error
    assert audio_path is not None  # audio kept even though transcription failed
    assert any("رونویسی نشد" in text for _, text in provider.sent)


async def test_download_failure_marks_failed_without_audio_path(
    pool, crypto, tmp_path, monkeypatch
):
    provider = FakeProvider()

    async def failing_download(file_id: str) -> bytes:
        raise ConnectionError("network down")

    provider.download_file = failing_download  # type: ignore[assignment]
    app, settings = _fake_app(pool, crypto, tmp_path, provider)

    update = make_voice_update(3, 999)
    msg = provider.parse_update(update)
    inbox_id = await _insert_pending_row(pool, msg)

    await orchestrator.transcribe_voice_job(app, inbox_id, msg, reply_message_id=None)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT transcript_status, transcript_error, audio_path FROM inbox WHERE id = %s",
            (inbox_id,),
        )
        status, error, audio_path = await cur.fetchone()

    assert status == "failed"
    assert "network down" in error
    assert audio_path is None
