"""Provider-agnostic contract. Nothing outside app/providers/bale.py may know
Bale's actual JSON shape — everything else talks to this dataclass/Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IncomingMessage:
    update_id: int | None
    message_id: int | None
    chat_id: int | None
    user_id: int | None
    text: str | None  # text or caption
    kind: str  # "text" | "voice" | "document" | "unknown"
    file_id: str | None
    file_unique_id: str | None
    duration_s: int | None
    is_forward: bool
    raw: dict


class MessagingProvider(Protocol):
    async def get_me(self) -> dict: ...

    async def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]: ...

    async def send_message(self, chat_id: int, text: str) -> dict: ...

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> bool:
        """Return True on success. Return False (never raise) if unsupported."""
        ...

    async def download_file(self, file_id: str) -> bytes: ...

    async def set_webhook(self, url: str, secret: str | None) -> dict: ...

    async def delete_webhook(self) -> dict: ...

    async def get_webhook_info(self) -> dict: ...

    def parse_update(self, raw: dict) -> IncomingMessage | None:
        """Return None on parse failure. Never raise."""
        ...
