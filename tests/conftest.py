"""Shared test fixtures.

Tests run against a real Postgres (DATABASE_URL, provided by CI as a service
container, or a local instance for development) with pgvector installed.
No live network and no live external providers — those are always mocked.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from app.crypto import Crypto
from app.db import apply_schema, create_pool

TEST_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://araz:araz@localhost:5432/araz_test"
)
TEST_FERNET_KEY = "A" * 31 + "="  # placeholder, replaced below with a real key
TEST_SESSION_SECRET = "s" * 40


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(TEST_DATABASE_URL)
    await apply_schema(p)
    yield p
    async with p.connection() as conn:
        await conn.execute("TRUNCATE inbox, app_settings, settings_audit, items RESTART IDENTITY")
    await p.close()


@pytest.fixture
def crypto():
    from cryptography.fernet import Fernet

    return Crypto(Fernet.generate_key().decode())
