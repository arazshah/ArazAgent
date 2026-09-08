"""All Bale-specific JSON shape knowledge lives here and only here.

The Bale Bot API is Telegram-shaped but not identical, and under-documented
(see scripts/probe_bale.py). Until the owner has run the probe against a real
bot and confirmed the shape, this file follows the brief's best-effort
assumption: updates carry `update_id` and a `message` object with
`message_id`, `chat.id`, `from.id`, `date`, and optionally `text`, `caption`,
`voice`, `audio`, `document`. Parsing uses `.get()` chains with fallbacks,
never hard indexing, so a shape mismatch degrades instead of crashing.
"""

from __future__ import annotations

import logging

import httpx

from app.providers.base import IncomingMessage

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0


class BaleClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._base = f"https://tapi.bale.ai/bot{token}"
        self._file_base = f"https://tapi.bale.ai/file/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_me(self) -> dict:
        resp = await self._client.get(f"{self._base}/getMe")
        resp.raise_for_status()
        return resp.json()

    async def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        params: dict[str, int] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        resp = await self._client.get(
            f"{self._base}/getUpdates", params=params, timeout=timeout + 10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", []) if isinstance(data, dict) else []

    async def send_message(self, chat_id: int, text: str) -> dict:
        resp = await self._client.post(
            f"{self._base}/sendMessage", json={"chat_id": chat_id, "text": text}
        )
        resp.raise_for_status()
        return resp.json()

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> bool:
        """Bale may not support editMessageText. Never raise — fall back to a new message."""
        try:
            resp = await self._client.post(
                f"{self._base}/editMessageText",
                json={"chat_id": chat_id, "message_id": message_id, "text": text},
            )
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("ok", True))
        except httpx.HTTPError as exc:
            logger.info("editMessageText unsupported or failed: %s", exc)
            return False

    async def download_file(self, file_id: str) -> bytes:
        resp = await self._client.get(
            f"{self._base}/getFile", params={"file_id": file_id}, timeout=DEFAULT_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        file_path = (data.get("result") or {}).get("file_path")
        if not file_path:
            raise ValueError(f"getFile returned no file_path for {file_id}")

        dl = await self._client.get(f"{self._file_base}/{file_path}", timeout=DOWNLOAD_TIMEOUT)
        dl.raise_for_status()
        return dl.content

    async def set_webhook(self, url: str, secret: str | None) -> dict:
        payload: dict[str, str] = {"url": url}
        if secret:
            payload["secret_token"] = secret
        resp = await self._client.post(f"{self._base}/setWebhook", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def delete_webhook(self) -> dict:
        resp = await self._client.post(f"{self._base}/deleteWebhook")
        resp.raise_for_status()
        return resp.json()

    async def get_webhook_info(self) -> dict:
        try:
            resp = await self._client.get(f"{self._base}/getWebhookInfo")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.info("getWebhookInfo unsupported or failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def parse_update(self, raw: dict) -> IncomingMessage | None:
        try:
            return _parse_update(raw)
        except Exception:  # noqa: BLE001 - a parser bug must never crash capture
            logger.error("failed to parse Bale update: %s", raw)
            return None


def _parse_update(raw: dict) -> IncomingMessage:
    update_id = raw.get("update_id")
    message = raw.get("message") or {}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    from_ = message.get("from") or {}
    user_id = from_.get("id")

    message_id = message.get("message_id")
    text = message.get("text") or message.get("caption")

    voice = message.get("voice") or message.get("audio")
    document = message.get("document")

    is_forward = bool(
        message.get("forward_date") or message.get("forward_from") or message.get("forward_origin")
    )

    file_id: str | None = None
    file_unique_id: str | None = None
    duration_s: int | None = None
    kind = "unknown"

    if voice:
        kind = "voice"
        file_id = voice.get("file_id")
        file_unique_id = voice.get("file_unique_id")
        duration_s = voice.get("duration")
    elif document:
        kind = "document"
        file_id = document.get("file_id")
        file_unique_id = document.get("file_unique_id")
    elif text is not None:
        kind = "text"

    return IncomingMessage(
        update_id=update_id,
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        kind=kind,
        file_id=file_id,
        file_unique_id=file_unique_id,
        duration_s=duration_s,
        is_forward=is_forward,
        raw=raw,
    )
