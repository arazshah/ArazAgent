"""Shared retry helper for transcription backends. 3 retries with exponential
backoff (2s, 8s, 30s) on transient errors (network, 429, 5xx) — 4 attempts
total. No retry on other 4xx errors; those are almost always misconfiguration
(bad API key, unsupported model) that another attempt would not fix.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RETRY_DELAYS: tuple[float, ...] = (2.0, 8.0, 30.0)


def _status_code_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
        return True
    status = _status_code_of(exc)
    if status is None:
        return False
    return status == 429 or status >= 500


async def call_with_retries[T](
    fn: Callable[[], Awaitable[T]], delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS
) -> T:
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0, *delays)):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below when not transient/exhausted
            last_exc = exc
            if not is_transient(exc):
                raise
            logger.warning("attempt %d failed transiently: %s", attempt + 1, exc)
    assert last_exc is not None
    raise last_exc
