"""Admin authentication: password hashing, signed sessions, CSRF, rate
limiting, and client IP resolution behind Coolify's proxy.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE_NAME = "araz_admin_session"
SESSION_MAX_AGE_SECONDS = 12 * 3600
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_CONSTANT_DELAY_SECONDS = 0.25

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001 - a corrupt/foreign hash must not crash login
        return False


def _serializer(session_secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(session_secret, salt="araz-admin-session")


def create_session_cookie(session_secret: str, epoch: int) -> str:
    return _serializer(session_secret).dumps({"epoch": epoch})


def verify_session_cookie(session_secret: str, token: str, current_epoch: int) -> bool:
    try:
        data = _serializer(session_secret).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("epoch") == current_epoch)


def csrf_token_for(session_secret: str, session_cookie_value: str) -> str:
    return hmac.new(
        session_secret.encode(), session_cookie_value.encode(), hashlib.sha256
    ).hexdigest()


def verify_csrf(session_secret: str, session_cookie_value: str, submitted_token: str) -> bool:
    expected = csrf_token_for(session_secret, session_cookie_value)
    return hmac.compare_digest(expected, submitted_token)


def client_ip(request: Request, trust_proxy_headers: bool) -> str:
    if trust_proxy_headers:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@dataclass
class LoginRateLimiter:
    """In-memory per-IP login attempt tracker.

    A single-process dict is acceptable for a single-user app; it resets on
    restart and does not share state across multiple app instances. That is
    an accepted limitation, not a bug, given the single-user threat model.
    """

    _attempts: dict[str, list[float]] = field(default_factory=dict)

    def is_locked_out(self, ip: str) -> bool:
        self._prune(ip)
        return len(self._attempts.get(ip, [])) >= LOGIN_MAX_ATTEMPTS

    def record_failure(self, ip: str) -> None:
        self._prune(ip)
        self._attempts.setdefault(ip, []).append(time.monotonic())

    def record_success(self, ip: str) -> None:
        self._attempts.pop(ip, None)

    def _prune(self, ip: str) -> None:
        cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
        attempts = self._attempts.get(ip, [])
        pruned = [t for t in attempts if t >= cutoff]
        if pruned:
            self._attempts[ip] = pruned
        else:
            self._attempts.pop(ip, None)
