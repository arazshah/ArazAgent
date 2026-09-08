"""Layer 2 configuration: DB-backed, editable from the admin UI.

Resolution order for every key: app_settings row -> environment variable ->
hardcoded default. The admin UI shows which layer a value came from so the
owner always knows where a value originates.

Secrets are Fernet-encrypted at rest. Decryption happens only in this module
and in app.crypto. Never log a decrypted value, never return one in an HTTP
response or template context — templates only ever see a masked hint.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Literal

from psycopg_pool import AsyncConnectionPool

from app.crypto import Crypto, mask_secret

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30

Source = Literal["db", "env", "default"]


@dataclass(frozen=True)
class SettingDef:
    key: str
    group: str
    is_secret: bool
    default: str | None
    env_var: str


def _env_var_for(key: str) -> str:
    return key.upper().replace(".", "_")


_DEFS: list[SettingDef] = [
    SettingDef("bale.bot_token", "bale", True, None, _env_var_for("bale.bot_token")),
    SettingDef("bale.allowed_user_ids", "bale", False, None, _env_var_for("bale.allowed_user_ids")),
    SettingDef("bale.mode", "bale", False, "webhook", _env_var_for("bale.mode")),
    SettingDef("bale.webhook_secret", "bale", True, None, _env_var_for("bale.webhook_secret")),
    SettingDef(
        "bale.public_base_url",
        "bale",
        False,
        "https://agent.araz.me",
        _env_var_for("bale.public_base_url"),
    ),
    SettingDef("system.poll_offset", "system", False, "0", _env_var_for("system.poll_offset")),
    SettingDef("llm.provider", "llm", False, "avalai", _env_var_for("llm.provider")),
    SettingDef(
        "llm.base_url",
        "llm",
        False,
        "https://api.avalai.ir/v1",
        _env_var_for("llm.base_url"),
    ),
    SettingDef("llm.api_key", "llm", True, None, _env_var_for("llm.api_key")),
    SettingDef("llm.chat_model", "llm", False, "gpt-4o-mini", _env_var_for("llm.chat_model")),
    SettingDef("llm.triage_enabled", "llm", False, "true", _env_var_for("llm.triage_enabled")),
    SettingDef(
        "llm.embedding_model",
        "llm",
        False,
        "text-embedding-3-small",
        _env_var_for("llm.embedding_model"),
    ),
    SettingDef(
        "llm.embedding_enabled", "llm", False, "true", _env_var_for("llm.embedding_enabled")
    ),
    SettingDef(
        "transcription.backend",
        "transcription",
        False,
        "avalai",
        _env_var_for("transcription.backend"),
    ),
    SettingDef(
        "transcription.model",
        "transcription",
        False,
        "whisper-1",
        _env_var_for("transcription.model"),
    ),
    SettingDef(
        "transcription.local_model",
        "transcription",
        False,
        "small",
        _env_var_for("transcription.local_model"),
    ),
    SettingDef(
        "transcription.language",
        "transcription",
        False,
        "fa",
        _env_var_for("transcription.language"),
    ),
    SettingDef(
        "system.kill_switch_target",
        "system",
        False,
        "100",
        _env_var_for("system.kill_switch_target"),
    ),
    SettingDef("admin.password_hash", "admin", True, None, "ADMIN_PASSWORD_HASH"),
    SettingDef("admin.session_epoch", "admin", False, "1", _env_var_for("admin.session_epoch")),
    SettingDef(
        "review.auto_enabled", "review", False, "false", _env_var_for("review.auto_enabled")
    ),
    SettingDef("review.send_time", "review", False, "21:00", _env_var_for("review.send_time")),
    # Internal bookkeeping (last date a daily review was actually sent) —
    # not exposed in the admin UI, same pattern as admin.session_epoch.
    SettingDef("review.last_sent_date", "review", False, "", _env_var_for("review.last_sent_date")),
    SettingDef("reminder.enabled", "reminder", False, "true", _env_var_for("reminder.enabled")),
    SettingDef("reminder.lead_hours", "reminder", False, "24", _env_var_for("reminder.lead_hours")),
    SettingDef("constitution.goals", "constitution", False, "", _env_var_for("constitution.goals")),
    SettingDef(
        "constitution.hard_rules",
        "constitution",
        False,
        "",
        _env_var_for("constitution.hard_rules"),
    ),
    SettingDef(
        "constitution.weekly_capacity_hours",
        "constitution",
        False,
        "40",
        _env_var_for("constitution.weekly_capacity_hours"),
    ),
]

DEFS_BY_KEY: dict[str, SettingDef] = {d.key: d for d in _DEFS}

# Some deployment platforms mangle '$' characters (argon2 hashes are full of
# them) when a value is stored or piped through a shell. A base64-encoded
# fallback env var sidesteps that entirely and is checked first.
_B64_ENV_FALLBACK: dict[str, str] = {"admin.password_hash": "ADMIN_PASSWORD_HASH_BASE64"}


class SettingsError(ValueError):
    """Raised when a stored or provided setting value is malformed."""


@dataclass(frozen=True)
class ResolvedSetting:
    value: str | None
    source: Source
    is_secret: bool
    masked_hint: str | None = None


class SettingsStore:
    def __init__(self, pool: AsyncConnectionPool, crypto: Crypto) -> None:
        self._pool = pool
        self._crypto = crypto
        self._cache: dict[str, tuple[float, ResolvedSetting]] = {}
        self._on_reload_keys: set[str] = {
            "bale.bot_token",
            "llm.api_key",
            "llm.base_url",
            "transcription.backend",
            "transcription.model",
            "transcription.local_model",
        }
        self._reload_callback = None

    def set_reload_callback(self, callback) -> None:
        """Called (no args, may be async) after a key requiring a client rebuild changes."""
        self._reload_callback = callback

    def _invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def _resolve_env_value(self, definition: SettingDef) -> str | None:
        b64_var = _B64_ENV_FALLBACK.get(definition.key)
        if b64_var:
            b64_value = os.environ.get(b64_var)
            if b64_value:
                try:
                    return base64.b64decode(b64_value).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    logger.warning("%s is not valid base64; ignoring", b64_var)
        return os.environ.get(definition.env_var)

    async def resolve(self, key: str) -> ResolvedSetting:
        cached = self._cache.get(key)
        if cached is not None:
            expiry, value = cached
            if time.monotonic() < expiry:
                return value

        definition = DEFS_BY_KEY.get(key)
        if definition is None:
            raise SettingsError(f"unknown setting key: {key}")

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT value_plain, value_enc, is_secret FROM app_settings WHERE key = %s",
                (key,),
            )
            row = await cur.fetchone()

        if row is not None:
            value_plain, value_enc, is_secret = row
            if value_enc is not None:
                plaintext = self._crypto.decrypt(bytes(value_enc))
                resolved = ResolvedSetting(plaintext, "db", True, mask_secret(plaintext))
            else:
                resolved = ResolvedSetting(value_plain, "db", is_secret)
        else:
            env_value = self._resolve_env_value(definition)
            if env_value is not None:
                resolved = ResolvedSetting(
                    env_value,
                    "env",
                    definition.is_secret,
                    mask_secret(env_value) if definition.is_secret else None,
                )
            else:
                resolved = ResolvedSetting(definition.default, "default", definition.is_secret)

        self._cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, resolved)
        return resolved

    async def get(self, key: str) -> str | None:
        return (await self.resolve(key)).value

    async def get_group(self, group: str) -> dict[str, ResolvedSetting]:
        keys = [d.key for d in _DEFS if d.group == group]
        return {key: await self.resolve(key) for key in keys}

    async def set(self, key: str, value: str, actor_ip: str | None = None) -> None:
        definition = DEFS_BY_KEY.get(key)
        if definition is None:
            raise SettingsError(f"unknown setting key: {key}")

        value_plain: str | None = None
        value_enc: bytes | None = None
        if definition.is_secret:
            value_enc = self._crypto.encrypt(value)
        else:
            value_plain = value

        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value_plain, value_enc, is_secret, group_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value_plain = EXCLUDED.value_plain,
                    value_enc = EXCLUDED.value_enc,
                    is_secret = EXCLUDED.is_secret,
                    updated_at = now()
                """,
                (key, value_plain, value_enc, definition.is_secret, definition.group),
            )
            await conn.execute(
                "INSERT INTO settings_audit (key, action, actor_ip) VALUES (%s, 'set', %s)",
                (key, actor_ip),
            )

        self._invalidate(key)
        await self._maybe_reload(key)

    async def clear(self, key: str, actor_ip: str | None = None) -> None:
        definition = DEFS_BY_KEY.get(key)
        if definition is None:
            raise SettingsError(f"unknown setting key: {key}")

        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = %s", (key,))
            await conn.execute(
                "INSERT INTO settings_audit (key, action, actor_ip) VALUES (%s, 'clear', %s)",
                (key, actor_ip),
            )

        self._invalidate(key)
        await self._maybe_reload(key)

    async def _maybe_reload(self, key: str) -> None:
        if key in self._on_reload_keys and self._reload_callback is not None:
            result = self._reload_callback()
            if hasattr(result, "__await__"):
                await result

    # --- Typed accessors -------------------------------------------------

    async def get_allowed_user_ids(self) -> list[int]:
        raw = await self.get("bale.allowed_user_ids")
        if not raw:
            return []
        try:
            return [int(part.strip()) for part in raw.split(",") if part.strip()]
        except ValueError as exc:
            raise SettingsError(
                "bale.allowed_user_ids must be a comma-separated list of integers"
            ) from exc

    async def get_mode(self) -> Literal["webhook", "polling"]:
        raw = await self.get("bale.mode") or "webhook"
        if raw not in ("webhook", "polling"):
            raise SettingsError("bale.mode must be 'webhook' or 'polling'")
        return raw  # type: ignore[return-value]

    async def get_kill_switch_target(self) -> int:
        raw = await self.get("system.kill_switch_target") or "100"
        try:
            value = int(raw)
        except ValueError as exc:
            raise SettingsError("system.kill_switch_target must be a positive integer") from exc
        if value <= 0:
            raise SettingsError("system.kill_switch_target must be a positive integer")
        return value

    async def get_poll_offset(self) -> int:
        raw = await self.get("system.poll_offset") or "0"
        try:
            return int(raw)
        except ValueError:
            return 0

    async def set_poll_offset(self, offset: int) -> None:
        await self.set("system.poll_offset", str(offset))
