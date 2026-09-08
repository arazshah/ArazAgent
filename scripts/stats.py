#!/usr/bin/env python3
"""Print capture stats and recover inbox rows stuck in
transcript_status='pending' for more than 10 minutes (e.g. after a
mid-transcription restart). A crude but sufficient recovery path — not a
real queue; the same logic backs the admin UI's "recover" button.

Usage:
    python scripts/stats.py [--recover]
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from app import tz
from app.bootstrap import load_bootstrap
from app.crypto import Crypto
from app.db import apply_schema, create_pool
from app.providers.registry import ProviderRegistry
from app.recovery import (
    recover_missing_embeddings,
    recover_stuck_transcriptions,
    recover_stuck_triage,
)
from app.settings_store import SettingsStore
from app.transcribe.orchestrator import transcribe_voice_job
from app.triage import triage_inbox_row


async def main() -> None:
    boot = load_bootstrap()
    pool = await create_pool(boot.database_url)
    await apply_schema(pool)

    settings = SettingsStore(pool, Crypto(boot.secret_encryption_key))
    registry = ProviderRegistry(settings)

    print("--- capture stats ---")
    print(f"total: {await tz.count_total(pool)}")
    print(f"today: {await tz.count_captured_today(pool)}")
    print("by source:")
    for source, count in sorted((await tz.count_by_source(pool)).items()):
        print(f"  {source}: {count}")
    print("by transcript_status:")
    for status, count in sorted((await tz.count_by_transcript_status(pool)).items()):
        print(f"  {status}: {count}")
    age = await tz.oldest_unprocessed_age_seconds(pool)
    if age is not None:
        print(f"oldest unprocessed: {int(age // 60)} minutes old")
    else:
        print("nothing unprocessed")
    print(f"pending triage: {await tz.count_pending_triage(pool)}")
    print(f"missing embeddings: {await tz.count_missing_embeddings(pool)}")

    if "--recover" in sys.argv:
        print("\n--- recovering stuck pending transcriptions, triage, and embeddings ---")

        async def _transcribe_voice(inbox_id, msg, reply_message_id):
            await transcribe_voice_job(app, inbox_id, msg, reply_message_id)

        async def _triage(inbox_id, text):
            await triage_inbox_row(pool, settings, inbox_id, text)

        app = SimpleNamespace(
            state=SimpleNamespace(
                pool=pool,
                settings=settings,
                bootstrap=boot,
                registry=registry,
                transcribe_voice=_transcribe_voice,
                triage=_triage,
            )
        )
        recovered = await recover_stuck_transcriptions(app)
        triaged = await recover_stuck_triage(app)
        embedded = await recover_missing_embeddings(app)
        print(f"recovered transcriptions: {recovered}")
        print(f"recovered triage: {triaged}")
        print(f"recovered embeddings: {embedded}")

    await registry.aclose()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
