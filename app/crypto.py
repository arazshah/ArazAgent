"""Fernet wrapper for secret-at-rest encryption.

This is the only place (besides settings_store) allowed to touch the raw
Fernet key or plaintext secret values.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class Crypto:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                "Failed to decrypt stored secret — SECRET_ENCRYPTION_KEY may have "
                "changed or the value is corrupt."
            ) from exc


def mask_secret(plaintext: str, visible: int = 4) -> str:
    """Render a masked hint like 'sk-…a3f9' for display in the admin UI."""
    if len(plaintext) <= visible:
        return "…" + plaintext
    return "…" + plaintext[-visible:]
