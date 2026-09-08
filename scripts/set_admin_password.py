#!/usr/bin/env python3
"""Set or rotate the admin password. This is the only way to do so — the
admin UI itself never offers a password-change form, so a compromised
session cannot rotate its own credential.

Usage:
    python scripts/set_admin_password.py
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys

from app.crypto import Crypto
from app.db import apply_schema, create_pool
from app.security import hash_password
from app.settings_store import SettingsStore


async def _write_to_db(password_hash: str) -> None:
    database_url = os.environ.get("DATABASE_URL")
    secret_key = os.environ.get("SECRET_ENCRYPTION_KEY")
    if not database_url or not secret_key:
        print(
            "DATABASE_URL and SECRET_ENCRYPTION_KEY must be set in the environment "
            "to write directly to the database.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    pool = await create_pool(database_url)
    await apply_schema(pool)
    store = SettingsStore(pool, Crypto(secret_key))
    await store.set("admin.password_hash", password_hash)
    # Rotating the password must invalidate every existing session.
    current_epoch = await store.get("admin.session_epoch") or "1"
    await store.set("admin.session_epoch", str(int(current_epoch) + 1))
    await pool.close()


def main() -> None:
    password = getpass.getpass("New admin password: ")
    if not password:
        print("password must not be empty", file=sys.stderr)
        raise SystemExit(1)
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("passwords did not match", file=sys.stderr)
        raise SystemExit(1)

    password_hash = hash_password(password)
    print(f"\nargon2 hash:\n{password_hash}\n")

    answer = input("Write directly to app_settings now? [y/N] ").strip().lower()
    if answer == "y":
        asyncio.run(_write_to_db(password_hash))
        print("written to app_settings; all existing sessions invalidated.")
    else:
        print(
            "Not written. Set ADMIN_PASSWORD_HASH to the hash above as a bootstrap "
            "fallback, or re-run and answer 'y' to write it to the database."
        )


if __name__ == "__main__":
    main()
