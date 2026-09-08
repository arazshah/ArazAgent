"""Test doubles for the provider layer. No live network."""

from __future__ import annotations

from app.providers.bale import BaleClient
from app.providers.base import IncomingMessage


class FakeProvider:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edits: list[tuple[int, int, str]] = []
        self._parser = BaleClient(token="fake")
        self.file_bytes = b"fake-ogg-audio-bytes"

    def parse_update(self, raw: dict) -> IncomingMessage | None:
        return self._parser.parse_update(raw)

    async def send_message(self, chat_id: int, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> bool:
        self.edits.append((chat_id, message_id, text))
        return True

    async def download_file(self, file_id: str) -> bytes:
        return self.file_bytes

    async def get_me(self) -> dict:
        return {"ok": True, "result": {"username": "fake_bot"}}

    async def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        return []

    async def set_webhook(self, url: str, secret: str | None) -> dict:
        return {"ok": True}

    async def delete_webhook(self) -> dict:
        return {"ok": True}

    async def get_webhook_info(self) -> dict:
        return {"ok": True, "result": {}}


def make_text_update(update_id: int, user_id: int, text: str, chat_id: int = 12345) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 1000,
            "date": 1717000000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False},
            "text": text,
        },
    }


def make_voice_update(update_id: int, user_id: int, chat_id: int = 12345) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 1000,
            "date": 1717000000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False},
            "voice": {
                "file_id": f"file-{update_id}",
                "file_unique_id": f"uniq-{update_id}",
                "duration": 5,
            },
        },
    }
