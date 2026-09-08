#!/usr/bin/env python3
"""Register or delete the Bale webhook, reading bale.public_base_url and
bale.webhook_secret from the settings store (not the environment) so it
always matches what the admin UI has configured.

Usage:
    python scripts/set_webhook.py
    python scripts/set_webhook.py --delete
"""

from __future__ import annotations

import asyncio
import sys

from app.crypto import Crypto
from app.db import apply_schema, create_pool
from app.providers.bale import BaleClient
from app.settings_store import SettingsStore


def _load_settings() -> tuple[str, str]:
    import os

    database_url = os.environ.get("DATABASE_URL")
    secret_key = os.environ.get("SECRET_ENCRYPTION_KEY")
    if not database_url or not secret_key:
        print("DATABASE_URL and SECRET_ENCRYPTION_KEY must be set.", file=sys.stderr)
        raise SystemExit(1)
    return database_url, secret_key


async def main() -> None:
    database_url, secret_key = _load_settings()
    pool = await create_pool(database_url)
    await apply_schema(pool)
    settings = SettingsStore(pool, Crypto(secret_key))

    token = await settings.get("bale.bot_token")
    if not token:
        print("bale.bot_token is not configured. Set it in the admin UI first.", file=sys.stderr)
        raise SystemExit(1)

    client = BaleClient(token=token)

    if "--delete" in sys.argv:
        result = await client.delete_webhook()
        print("deleteWebhook:", result)
    else:
        base_url = await settings.get("bale.public_base_url")
        secret = await settings.get("bale.webhook_secret")
        if not base_url or not secret:
            print(
                "bale.public_base_url and bale.webhook_secret must be configured "
                "in the admin UI first.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        url = f"{base_url}/webhook/{secret}"
        result = await client.set_webhook(url, secret)
        print("setWebhook:", result)
        if not result.get("ok", True):
            print(
                "\nBale rejected setWebhook, or does not support it. "
                "Consider switching to bale.mode = polling in the admin UI.",
                file=sys.stderr,
            )

    info = await client.get_webhook_info()
    print("getWebhookInfo:", info)

    await client.aclose()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
