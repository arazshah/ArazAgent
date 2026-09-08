"""Orchestrates the voice capture background job: download the audio (kept
permanently — it is the source of truth if the transcript is wrong),
transcribe it via the configured backend, and update the inbox row. Never
raises out of the job — on any failure the row is marked 'failed' with the
audio path preserved, and the user is told transcription didn't work.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from psycopg_pool import AsyncConnectionPool

from app.providers.base import IncomingMessage, MessagingProvider
from app.transcribe.avalai import AvalAITranscriber
from app.transcribe.local import LocalTranscriber
from app.triage import triage_inbox_row

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

FAILURE_TEXT = "⚠️ #{inbox_id} ثبت شد ولی رونویسی نشد. فایل صوتی نگه داشته شد."


def audio_path_for(audio_dir: str, unique_part: str, when: datetime | None = None) -> Path:
    now = when or datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return Path(audio_dir) / now.strftime("%Y") / now.strftime("%m") / f"{stamp}-{unique_part}.oga"


async def build_backend(app: FastAPI) -> tuple[object, str]:
    settings = app.state.settings
    backend_name = await settings.get("transcription.backend") or "avalai"

    if backend_name == "local":
        model_size = await settings.get("transcription.local_model") or "small"
        return LocalTranscriber(model_size), "local"

    base_url = await settings.get("llm.base_url")
    api_key = await settings.get("llm.api_key")
    model = await settings.get("transcription.model") or "whisper-1"
    if not base_url or not api_key:
        raise RuntimeError("AvalAI transcription requires llm.base_url and llm.api_key")
    return AvalAITranscriber(base_url, api_key, model), "avalai"


async def _mark_failed(pool: AsyncConnectionPool, inbox_id: int, error: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE inbox SET transcript_status = 'failed', transcript_error = %s WHERE id = %s",
            (error, inbox_id),
        )


async def _reply_failure(provider: MessagingProvider, chat_id: int | None, inbox_id: int) -> None:
    if chat_id is None:
        return
    await provider.send_message(chat_id, FAILURE_TEXT.format(inbox_id=inbox_id))


async def transcribe_voice_job(
    app: FastAPI, inbox_id: int, msg: IncomingMessage, reply_message_id: int | None
) -> None:
    pool: AsyncConnectionPool = app.state.pool
    settings = app.state.settings
    boot = app.state.bootstrap

    provider = await app.state.registry.get_bale_client()
    if provider is None or msg.file_id is None:
        await _mark_failed(pool, inbox_id, "no provider configured or missing file_id")
        return

    try:
        audio_bytes = await provider.download_file(msg.file_id)
    except Exception as exc:  # noqa: BLE001 - degrade, never drop
        logger.exception("audio download failed for inbox_id=%s", inbox_id)
        await _mark_failed(pool, inbox_id, str(exc)[:200])
        await _reply_failure(provider, msg.chat_id, inbox_id)
        return

    audio_path = audio_path_for(boot.audio_dir, msg.file_unique_id or f"inbox{inbox_id}")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(audio_bytes)

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE inbox SET audio_path = %s, audio_duration_s = %s WHERE id = %s",
            (str(audio_path), msg.duration_s, inbox_id),
        )

    try:
        backend, backend_name = await build_backend(app)
        language = await settings.get("transcription.language") or "fa"
        text = await backend.transcribe(audio_bytes, language)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - degrade, never drop
        logger.exception("transcription failed for inbox_id=%s", inbox_id)
        await _mark_failed(pool, inbox_id, str(exc)[:200])
        await _reply_failure(provider, msg.chat_id, inbox_id)
        return

    async with pool.connection() as conn:
        await conn.execute(
            """
            UPDATE inbox SET raw_text = %s, transcript_status = 'done',
                              transcript_backend = %s
            WHERE id = %s
            """,
            (text, backend_name, inbox_id),
        )

    if msg.chat_id is not None:
        truncated = text[:500]
        reply_text = f"#{inbox_id}: {truncated}"
        edited = False
        if reply_message_id is not None:
            edited = await provider.edit_message_text(msg.chat_id, reply_message_id, reply_text)
        if not edited:
            await provider.send_message(msg.chat_id, reply_text)

    # Phase 2: classify the transcript into an `items` row. This sets
    # inbox.processed_at on success — transcription completing is not the
    # same as the row being triaged.
    notify = None
    if msg.chat_id is not None:
        chat_id = msg.chat_id

        async def notify(message: str) -> None:  # noqa: F811 - conditional definition
            await provider.send_message(chat_id, message)

    await triage_inbox_row(pool, settings, inbox_id, text, notify=notify)
