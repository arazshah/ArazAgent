"""Direct unit coverage for app.retry — previously exercised only
indirectly (and incompletely: see the APIConnectionError fix in this same
change) through app/transcribe/avalai.py's stubbed-out tests. No live
network; delays are passed as (0, 0, 0) so these run instantly.
"""

from __future__ import annotations

import httpx
from openai import APIConnectionError, APITimeoutError

from app import retry

NO_DELAY = (0.0, 0.0, 0.0)


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.avalai.ir/v1/chat/completions")


class _FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def test_is_transient_true_for_connection_error():
    assert retry.is_transient(APIConnectionError(request=_fake_request())) is True


def test_is_transient_true_for_timeout_error():
    assert retry.is_transient(APITimeoutError(request=_fake_request())) is True


def test_is_transient_true_for_httpx_network_error():
    assert retry.is_transient(httpx.ConnectError("boom")) is True


def test_is_transient_true_for_429_and_5xx():
    assert retry.is_transient(_FakeStatusError(429)) is True
    assert retry.is_transient(_FakeStatusError(500)) is True
    assert retry.is_transient(_FakeStatusError(503)) is True


def test_is_transient_false_for_other_4xx():
    assert retry.is_transient(_FakeStatusError(400)) is False
    assert retry.is_transient(_FakeStatusError(401)) is False
    assert retry.is_transient(_FakeStatusError(404)) is False


def test_is_transient_false_for_plain_exception():
    assert retry.is_transient(ValueError("not an API error")) is False


async def test_call_with_retries_succeeds_first_try():
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    result = await retry.call_with_retries(fn, delays=NO_DELAY)

    assert result == "ok"
    assert len(calls) == 1


async def test_call_with_retries_recovers_after_transient_failures():
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise APIConnectionError(request=_fake_request())
        return "ok"

    result = await retry.call_with_retries(fn, delays=NO_DELAY)

    assert result == "ok"
    assert len(calls) == 3


async def test_call_with_retries_gives_up_after_exhausting_delays():
    calls = []

    async def fn():
        calls.append(1)
        raise APIConnectionError(request=_fake_request())

    try:
        await retry.call_with_retries(fn, delays=NO_DELAY)
    except APIConnectionError:
        pass
    else:
        raise AssertionError("expected APIConnectionError to propagate")

    assert len(calls) == 4  # 1 initial attempt + 3 retries


async def test_call_with_retries_does_not_retry_non_transient_errors():
    calls = []

    async def fn():
        calls.append(1)
        raise _FakeStatusError(401)

    try:
        await retry.call_with_retries(fn, delays=NO_DELAY)
    except _FakeStatusError:
        pass
    else:
        raise AssertionError("expected _FakeStatusError to propagate immediately")

    assert len(calls) == 1
