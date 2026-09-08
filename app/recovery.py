"""Recovery for inbox/items rows stuck mid-pipeline: transcript_status=
'pending' after a mid-transcription restart, inbox.processed_at IS NULL
after a mid-triage restart, or items.embedding IS NULL after a mid-embedding
restart. A crude but sufficient recovery path — not a real queue. Shared by
the admin "recover" button and scripts/stats.py.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.embeddings import embed_item

logger = logging.getLogger(__name__)

STUCK_AFTER_MINUTES = 10


async def recover_stuck_transcriptions(app: FastAPI) -> int:
    pool = app.state.pool
    transcribe_voice = getattr(app.state, "transcribe_voice", None)

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, raw_update FROM inbox
            WHERE transcript_status = 'pending'
              AND captured_at < now() - interval '10 minutes'
            """
        )
        rows = await cur.fetchall()

    if not rows:
        return 0

    if transcribe_voice is None:
        logger.warning(
            "recovery found %d stuck row(s) but no transcription backend is configured",
            len(rows),
        )
        return 0

    registry = app.state.registry
    provider = await registry.get_bale_client()
    if provider is None:
        logger.warning("recovery found %d stuck row(s) but no Bale client is configured", len(rows))
        return 0

    recovered = 0
    for inbox_id, raw_update in rows:
        msg = provider.parse_update(raw_update)
        if msg is None:
            logger.error("recovery: could not re-parse raw_update for inbox_id=%s", inbox_id)
            continue
        await transcribe_voice(inbox_id, msg, None)
        recovered += 1

    return recovered


async def recover_stuck_triage(app: FastAPI) -> int:
    pool = app.state.pool
    triage = getattr(app.state, "triage", None)
    if triage is None:
        return 0

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, raw_text FROM inbox
            WHERE processed_at IS NULL
              AND transcript_status IN ('n/a', 'done')
              AND captured_at < now() - interval '10 minutes'
            """
        )
        rows = await cur.fetchall()

    for inbox_id, raw_text in rows:
        await triage(inbox_id, raw_text)

    return len(rows)


async def recover_missing_embeddings(app: FastAPI) -> int:
    pool = app.state.pool
    settings = app.state.settings

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, title FROM items
            WHERE embedding IS NULL
              AND created_at < now() - interval '10 minutes'
            """
        )
        rows = await cur.fetchall()

    for item_id, title in rows:
        await embed_item(pool, settings, item_id, title)

    return len(rows)
