"""Owns the long-lived Bale client and rebuilds it when relevant settings
change, so a settings edit in the admin UI never requires a restart.
"""

from __future__ import annotations

import asyncio

from app.providers.bale import BaleClient
from app.settings_store import SettingsStore


class ProviderRegistry:
    def __init__(self, settings: SettingsStore) -> None:
        self._settings = settings
        self._client: BaleClient | None = None
        self._lock = asyncio.Lock()

    async def get_bale_client(self) -> BaleClient | None:
        async with self._lock:
            if self._client is None:
                token = await self._settings.get("bale.bot_token")
                if not token:
                    return None
                self._client = BaleClient(token=token)
            return self._client

    async def reload(self) -> None:
        """Force the next get_bale_client() call to rebuild with fresh settings."""
        async with self._lock:
            old = self._client
            self._client = None
        if old is not None:
            await old.aclose()

    async def aclose(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
        if client is not None:
            await client.aclose()
