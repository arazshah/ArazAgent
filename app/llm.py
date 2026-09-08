"""AvalAI chat client. Phase 1 used this only for the admin "test connection"
health check. Phase 2 adds classify_capture(), the one LLM call made on
captured content (see app/triage.py) — everything else in the app still
treats captured text as opaque.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from openai import AsyncOpenAI

from app.tz import TEHRAN

logger = logging.getLogger(__name__)

VALID_ITEM_TYPES = ("task", "note", "idea", "event")

_TRIAGE_SYSTEM_PROMPT = """تو بخشی از یک دستیار شخصی هستی که پیام‌های ثبت‌شده‌ی کاربر را \
دسته‌بندی می‌کند. امروز {today} است (تقویم میلادی، منطقه‌ی زمانی تهران).

برای متن ورودی، دقیقاً یک شیء JSON با این کلیدها برگردان و هیچ متن دیگری \
(از جمله ```) ننویس:

{{
  "type": یکی از "task" (کاری قابل‌انجام)، "note" (یادداشت/اطلاعات)، \
"idea" (ایده)، "event" (رویداد/قرار زمان‌دار),
  "title": خلاصه‌ی کوتاه فارسی (حداکثر ۸۰ کاراکتر),
  "project": نام پروژه‌ی مرتبط اگر از متن مشخص است، وگرنه null,
  "goal_key": یک شناسه‌ی کوتاه لاتین (snake_case) برای هدف مرتبط اگر مشخص \
است، وگرنه null,
  "effort_minutes": تخمین زمان لازم به دقیقه (عدد صحیح) اگر قابل‌تخمین \
است، وگرنه null,
  "deadline": تاریخ سررسید به‌صورت YYYY-MM-DD اگر متن به آن اشاره دارد، \
وگرنه null,
  "decision_reason": یک جمله‌ی کوتاه فارسی که دلیل این دسته‌بندی را \
توضیح می‌دهد
}}
"""


def _strip_code_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip()
    return content


async def classify_capture(base_url: str, api_key: str, model: str, text: str) -> dict:
    """Classify one captured text into the `items` shape (see db/schema.sql).

    Never raises — any failure (network, malformed JSON, an empty response)
    comes back as {"error": ...} so the caller (app/triage.py) can leave the
    inbox row unprocessed for a later retry instead of losing it.
    """
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=30)
    try:
        today = datetime.now(TEHRAN).date().isoformat()
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _TRIAGE_SYSTEM_PROMPT.format(today=today)},
                {"role": "user", "content": text},
            ],
            temperature=0,
        )
        content = resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 - degrade, never drop
        logger.warning("triage classification call failed: %s", exc)
        return {"error": str(exc)}
    finally:
        await client.close()

    try:
        parsed = json.loads(_strip_code_fence(content))
    except (ValueError, TypeError) as exc:
        return {"error": f"could not parse LLM response as JSON: {exc}"}
    if not isinstance(parsed, dict):
        return {"error": "LLM response was not a JSON object"}
    return parsed


async def embed_text(base_url: str, api_key: str, model: str, text: str) -> list[float] | None:
    """One embedding call. Never raises — returns None on any failure so the
    caller (app/embeddings.py) can leave the row unembedded for a later
    retry instead of losing it or crashing the background task.
    """
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=30)
    try:
        resp = await client.embeddings.create(model=model, input=text[:8000])
        return resp.data[0].embedding
    except Exception as exc:  # noqa: BLE001 - degrade, never drop
        logger.warning("embedding call failed: %s", exc)
        return None
    finally:
        await client.close()


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
