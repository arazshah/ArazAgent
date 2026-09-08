#!/usr/bin/env python3
"""Probe the real Bale Bot API shape.

The Bale Bot API is Telegram-shaped but not identical, and under-documented.
This script talks to it directly with no database and no app dependencies,
so the owner can run it standalone, send the bot a text message and a voice
note, and paste the resulting JSON back for the parser in
app/providers/bale.py to be shaped against reality instead of assumptions.

Usage:
    export BALE_BOT_TOKEN=...
    python scripts/probe_bale.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "probe_output.jsonl"


def _base_url(token: str) -> str:
    return f"https://tapi.bale.ai/bot{token}"


async def probe() -> None:
    token = os.environ.get("BALE_BOT_TOKEN")
    if not token:
        print("FATAL: set BALE_BOT_TOKEN in the environment.", file=sys.stderr)
        raise SystemExit(1)

    base = _base_url(token)

    async with httpx.AsyncClient(timeout=30) as client:
        print("--- getMe ---")
        resp = await client.get(f"{base}/getMe")
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))

        print()
        print("--- getUpdates (long-polling loop, Ctrl+C to stop) ---")
        print(f"appending each raw update to {OUTPUT_PATH}")
        offset: int | None = None
        while True:
            params: dict[str, int] = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            try:
                resp = await client.get(f"{base}/getUpdates", params=params, timeout=40)
                data = resp.json()
            except httpx.HTTPError as exc:
                print(f"request failed: {exc}", file=sys.stderr)
                await asyncio.sleep(2)
                continue

            updates = data.get("result", [])
            for update in updates:
                print(json.dumps(update, indent=2, ensure_ascii=False))
                with OUTPUT_PATH.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(update, ensure_ascii=False) + "\n")
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1

            if not updates:
                await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(probe())
    except KeyboardInterrupt:
        print("\nstopped.")
