"""Logging filter that redacts values matching known secret patterns as a
last line of defense — never the primary control (that's not logging
decrypted secrets in the first place; see app/settings_store.py).
"""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"Bearer [A-Za-z0-9._-]{16,}"),
    re.compile(r"tapi\.bale\.ai/bot[0-9A-Za-z_:-]{8,}"),
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
]

REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True


def install(root_logger: logging.Logger | None = None) -> None:
    logger = root_logger or logging.getLogger()
    logger.addFilter(RedactSecretsFilter())
