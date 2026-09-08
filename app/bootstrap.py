"""Layer 1 configuration: environment-only, immutable at runtime.

Only what is needed to start the process and reach the database lives here.
These can never be edited from the admin UI, because the UI itself depends
on them (database connection, secret encryption key, session signing key).
Everything else belongs in app.settings_store.
"""

from __future__ import annotations

import sys

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_GENERATION_HINTS = {
    "DATABASE_URL": "postgresql://user:pass@host:5432/dbname",
    "SECRET_ENCRYPTION_KEY": (
        "python -c \"from cryptography.fernet import Fernet; "
        'print(Fernet.generate_key().decode())"'
    ),
    "SESSION_SECRET": "python -c \"import secrets; print(secrets.token_urlsafe(48))\"",
}


class Bootstrap(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(alias="DATABASE_URL")
    secret_encryption_key: str = Field(alias="SECRET_ENCRYPTION_KEY")
    session_secret: str = Field(alias="SESSION_SECRET")
    admin_password_hash: str | None = Field(default=None, alias="ADMIN_PASSWORD_HASH")
    audio_dir: str = Field(default="/app/data/audio", alias="AUDIO_DIR")
    tz: str = Field(default="Asia/Tehran", alias="TZ")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    admin_path: str = Field(default="/admin", alias="ADMIN_PATH")
    trust_proxy_headers: bool = Field(default=True, alias="TRUST_PROXY_HEADERS")
    git_sha: str = Field(default="unknown", alias="GIT_SHA")

    @field_validator("session_secret")
    @classmethod
    def _session_secret_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        return v


def load_bootstrap() -> Bootstrap:
    """Load and validate bootstrap config, failing fast and loudly."""
    try:
        return Bootstrap()  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001 - we want to report every field at once
        print("FATAL: missing or invalid bootstrap configuration.", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("", file=sys.stderr)
        print("Generate the required secrets with:", file=sys.stderr)
        for name, hint in _GENERATION_HINTS.items():
            print(f"  {name}: {hint}", file=sys.stderr)
        raise SystemExit(1) from exc
