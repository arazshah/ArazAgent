"""Use-case: handle_update(). Never lose a capture — if transcription fails,
if the LLM gateway is down, if the audio download fails, the row is still
written and the user still gets a reply. Degrade, never drop.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from app import commands, tz
from app.providers.base import IncomingMessage, MessagingProvider
from app.settings_store import SettingsStore

logger = logging.getLogger(__name__)

TranscribeJob = Callable[[int, IncomingMessage], Awaitable[None]]


@dataclass
class CaptureContext:
    pool: AsyncConnectionPool
    settings: SettingsStore
    provider: MessagingProvider
    schedule_background: Callable[[Callable[[], Awaitable[None]]], None]
    transcribe_voice: TranscribeJob | None = None


async def handle_update(raw: dict, ctx: CaptureContext) -> None:
    started = time.monotonic()
    msg = ctx.provider.parse_update(raw)
    if msg is None:
        logger.error("dropping unparseable update: %s", raw)
        return

    allowed = await ctx.settings.get_allowed_user_ids()
    if not allowed:
        logger.error(
            "bale.allowed_user_ids is empty — configure it in the admin UI; dropping update"
        )
        return
    if msg.user_id not in allowed:
        logger.warning("dropping update from disallowed user_id=%s", msg.user_id)
        return

    if msg.chat_id is None:
        logger.error("update has no chat_id, cannot reply — dropping: %s", raw)
        return

    if msg.text and msg.text.startswith("/"):
        await commands.dispatch(msg, ctx)
        return

    if msg.kind == "voice":
        inbox_id = await _handle_voice(msg, ctx)
    elif msg.kind == "document":
        inbox_id = await _handle_document(msg, ctx)
    else:
        inbox_id = await _handle_text(msg, ctx)

    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "update_id=%s inbox_id=%s kind=%s latency_ms=%d",
        msg.update_id,
        inbox_id,
        msg.kind,
        latency_ms,
    )


async def _insert_inbox_row(
    ctx: CaptureContext,
    *,
    source: str,
    raw_text: str | None,
    transcript_status: str,
    msg: IncomingMessage,
) -> int | None:
    """Insert the inbox row, ON CONFLICT DO NOTHING for idempotency. Returns
    the new row id, or None if this update_id was already captured (a
    redelivery) — the caller must reply nothing in that case.
    """
    async with ctx.pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO inbox (
                source, raw_text, transcript_status, provider,
                provider_update_id, provider_message_id, provider_chat_id, raw_update
            )
            VALUES (%s, %s, %s, 'bale', %s, %s, %s, %s)
            ON CONFLICT (provider, provider_update_id) WHERE provider_update_id IS NOT NULL
            DO NOTHING
            RETURNING id
            """,
            (
                source,
                raw_text,
                transcript_status,
                msg.update_id,
                msg.message_id,
                msg.chat_id,
                json.dumps(msg.raw),
            ),
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def _handle_text(msg: IncomingMessage, ctx: CaptureContext) -> int | None:
    assert msg.chat_id is not None
    source = "bale_forward" if msg.is_forward else "bale_text"
    inbox_id = await _insert_inbox_row(
        ctx, source=source, raw_text=msg.text, transcript_status="n/a", msg=msg
    )
    if inbox_id is None:
        return None
    count = await tz.count_captured_today(ctx.pool)
    await ctx.provider.send_message(msg.chat_id, f"✅ #{inbox_id} · امروز {count}")
    return inbox_id


async def _handle_document(msg: IncomingMessage, ctx: CaptureContext) -> int | None:
    assert msg.chat_id is not None
    inbox_id = await _insert_inbox_row(
        ctx, source="bale_document", raw_text=msg.text, transcript_status="n/a", msg=msg
    )
    if inbox_id is None:
        return None
    await ctx.provider.send_message(msg.chat_id, f"✅ #{inbox_id} (فایل — بدون رونویسی)")
    return inbox_id


async def _handle_voice(msg: IncomingMessage, ctx: CaptureContext) -> int | None:
    assert msg.chat_id is not None
    inbox_id = await _insert_inbox_row(
        ctx, source="bale_voice", raw_text=None, transcript_status="pending", msg=msg
    )
    if inbox_id is None:
        return None
    await ctx.provider.send_message(msg.chat_id, f"🎙 #{inbox_id} ثبت شد · در حال رونویسی…")

    if ctx.transcribe_voice is not None:
        job = ctx.transcribe_voice

        async def run() -> None:
            await job(inbox_id, msg)

        ctx.schedule_background(run)
    else:
        logger.warning(
            "no transcribe_voice handler configured; inbox_id=%s stays pending", inbox_id
        )

    return inbox_id
