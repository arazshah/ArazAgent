"""Recovery for inbox rows stuck in transcript_status='pending', e.g. after a
mid-transcription restart. A crude but sufficient recovery path — not a real
queue. Shared by the admin "recover" button and scripts/stats.py.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

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
        await transcribe_voice(inbox_id, msg)
        recovered += 1

    return recovered
