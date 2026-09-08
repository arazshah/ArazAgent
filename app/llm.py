"""AvalAI chat client. Phase 1 only uses this for the admin "test connection"
health check — no LLM calls are made on captured content in Phase 1.
"""

from __future__ import annotations

import time

from openai import AsyncOpenAI


async def test_chat_connection(base_url: str, api_key: str, model: str) -> dict:
    """One-token chat completion, returning latency and model name. Never
    raises — callers render the dict, including an 'error' key on failure.
    """
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=30)
    started = time.monotonic()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "model": resp.model,
        }
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin, never raised
        return {"ok": False, "error": str(exc)}
    finally:
        await client.close()
