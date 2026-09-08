"""AvalAI chat client. Phase 1 used this only for the admin "test connection"
health check. Phase 2 added classify_capture(), the one LLM call made on
captured content (see app/triage.py); Phase 4 added embed_text() (see
app/embeddings.py). Everything else in the app still treats captured text
as opaque.

classify_capture() and embed_text() retry transient failures (app.retry,
Phase 7) since they run as background jobs where a few extra seconds is
free — test_chat_connection() deliberately does not, since a human is
watching a spinner waiting for that one.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from openai import AsyncOpenAI

from app.jalali import today_jalali_str
from app.retry import call_with_retries
from app.tz import TEHRAN

logger = logging.getLogger(__name__)

VALID_ITEM_TYPES = ("task", "note", "idea", "event")
VALID_DECISIONS = ("do_now", "schedule", "delegate", "archive", "decline")

_TRIAGE_SYSTEM_PROMPT = """تو دروازه‌بان آراز هستی، نه فقط یک دسته‌بند. کارت این نیست که \
کمک کنی همه‌چیز انجام شود؛ کارت این است که جلوی انجام‌شدن چیزهای کم‌ارزش را بگیری.

امروز {today} است به تقویم میلادی، برابر با {today_jalali} به تقویم شمسی \
(منطقه‌ی زمانی تهران).

اهداف ۱۲‌ماهه (هرچه وزن بالاتر، اهمیت بیشتر):
{goals_block}

قوانین سخت (این‌ها قابل‌نقض نیستند):
{hard_rules_block}

ظرفیت باقی‌مانده تقریباً {remaining_capacity_hours:.0f} ساعت از \
{weekly_capacity_hours} ساعت ظرفیت هفتگی است. اگر این عدد صفر یا منفی است، \
فقط "schedule" یا "decline" پیشنهاد بده، نه "do_now".

کاربر ممکن است به تاریخ به هر شکلی اشاره کند: شمسی ("۱۵ مهر", "دوشنبه‌ی \
بعد")، میلادی، یا نسبی ("فردا", "هفته‌ی دیگر"). آن را با توجه به تاریخ امروز \
(هر دو تقویم بالا) به تاریخ میلادی دقیق تبدیل کن.

برای متن ورودی، دقیقاً یک شیء JSON با این کلیدها برگردان و هیچ متن دیگری \
(از جمله ```) ننویس:

{{
  "type": یکی از "task" (کاری قابل‌انجام)، "note" (یادداشت/اطلاعات)، \
"idea" (ایده)، "event" (رویداد/قرار زمان‌دار),
  "title": خلاصه‌ی کوتاه فارسی (حداکثر ۸۰ کاراکتر),
  "project": نام پروژه‌ی مرتبط اگر از متن مشخص است، وگرنه null,
  "goal_key": عنوان دقیق یکی از اهداف بالا اگر این آیتم به آن مرتبط است، \
وگرنه null,
  "effort_minutes": تخمین زمان لازم به دقیقه (عدد صحیح) اگر قابل‌تخمین \
است، وگرنه null,
  "deadline": تاریخ سررسید به‌صورت YYYY-MM-DD **میلادی** اگر متن به آن \
اشاره دارد، وگرنه null,
  "score": عدد صحیح ۰ تا ۲۵ بر اساس مجموع این معیارها (هرکدام ۰ تا ۵): \
هم‌راستایی با اهداف بالا (وزن ۳)، اثر مرکب/بلندمدت (وزن ۳)، ارزش اقتصادی \
نسبت به زمان (وزن ۲)، برگشت‌ناپذیری در صورت انجام‌نشدن (وزن ۲)، منهای \
هزینه‌ی واقعی زمانی و بار ذهنی (وزن ۲، منفی),
  "decision": یکی از "do_now" (همین حالا انجام بده)، "schedule" \
(زمان‌بندی کن)، "delegate" (بسپار)، "archive" (بایگانی کن)، "decline" \
(رد کن),
  "decision_reason": یک جمله‌ی کوتاه فارسی که این تصمیم را توضیح می‌دهد,
  "what_to_drop_instead": اگر decision برابر "do_now" است، این فیلد \
**الزامی** است — دقیقاً بگو به‌جای این چه کار باز دیگری کنار گذاشته یا \
دیرتر انجام می‌شود، چون هر "بله"ی جدید باید با یک "نه" به چیز دیگری همراه \
باشد. اگر واقعاً هیچ‌چیز برای کنار گذاشتن نیست، decision را "do_now" \
نگذار و به‌جایش "schedule" بگذار. برای هر decision دیگری این مقدار null \
است.
}}

اگر آیتم با هیچ‌کدام از اهداف بالا ارتباط ندارد، پیش‌فرض "decline" یا \
"archive" است، مگر اینکه یک تعهد فوری و مشخص باشد (مثلاً قرار با فرد دیگری). \
اگر هیچ هدفی هنوز تعریف نشده، بر اساس قضاوت عمومی از اثر/ارزش/برگشت‌ناپذیری \
امتیاز بده.
"""


def _strip_code_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip()
    return content


def _format_prompt(constitution: dict) -> str:
    goals = constitution.get("goals") or []
    goals_block = (
        "\n".join(f"- {title} (وزن {weight})" for title, weight in goals)
        or "(هنوز هدفی تعریف نشده — بر اساس قضاوت عمومی امتیاز بده.)"
    )
    hard_rules_block = constitution.get("hard_rules") or "(قانون سخت خاصی تعریف نشده.)"

    return _TRIAGE_SYSTEM_PROMPT.format(
        today=datetime.now(TEHRAN).date().isoformat(),
        today_jalali=today_jalali_str(),
        goals_block=goals_block,
        hard_rules_block=hard_rules_block,
        remaining_capacity_hours=constitution.get("remaining_capacity_hours", 0.0),
        weekly_capacity_hours=constitution.get("weekly_capacity_hours", 0),
    )


async def classify_capture(
    base_url: str, api_key: str, model: str, text: str, constitution: dict
) -> dict:
    """Classify one captured text into the `items` shape (see db/schema.sql),
    scoring and gatekeeping it against `constitution` (see
    app.constitution.build_constitution_context) rather than just labeling
    it.

    Never raises — any failure (network, malformed JSON, an empty response)
    comes back as {"error": ...} so the caller (app/triage.py) can leave the
    inbox row unprocessed for a later retry instead of losing it.
    """
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=30)
    try:
        prompt = _format_prompt(constitution)

        async def call():
            return await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0,
            )

        resp = await call_with_retries(call)
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

        async def call():
            return await client.embeddings.create(model=model, input=text[:8000])

        resp = await call_with_retries(call)
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
