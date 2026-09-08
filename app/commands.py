"""Bot commands: /start /today /stats. No admin commands over the bot — all
configuration happens in the web UI, so a compromised messaging account can
never change settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import tz
from app.providers.base import IncomingMessage

if TYPE_CHECKING:
    from app.capture import CaptureContext

START_TEXT = (
    "لایه ثبت فعال است.\n\n"
    "هر چیزی به ذهنت آمد بفرست — متن، ویس، فوروارد، لینک.\n"
    "دسته‌بندی نکن. توضیح نده. فقط بفرست.\n\n"
    "/today برای شمارش\n"
    "/items برای دیدن آخرین آیتم‌های دسته‌بندی‌شده"
)

UNKNOWN_COMMAND_TEXT = "دستور ناشناخته."

NO_ITEMS_TEXT = "هنوز هیچ آیتمی دسته‌بندی نشده است."

ITEMS_LIMIT = 10

_TYPE_LABEL = {"task": "📌 کار", "note": "📝 یادداشت", "idea": "💡 ایده", "event": "📅 رویداد"}


async def dispatch(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.text is not None
    assert msg.chat_id is not None
    command = msg.text.strip().split()[0].lower().lstrip("/")

    if command == "start":
        await ctx.provider.send_message(msg.chat_id, START_TEXT)
    elif command == "today":
        await _today(msg, ctx)
    elif command == "stats":
        await _stats(msg, ctx)
    elif command == "items":
        await _items(msg, ctx)
    else:
        await ctx.provider.send_message(msg.chat_id, UNKNOWN_COMMAND_TEXT)


async def _today(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    today_count = await tz.count_captured_today(ctx.pool)
    total = await tz.count_total(ctx.pool)
    target = await ctx.settings.get_kill_switch_target()
    text = f"امروز: {today_count}\nمجموع: {total} / {target}"
    await ctx.provider.send_message(msg.chat_id, text)


async def _stats(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    by_source = await tz.count_by_source(ctx.pool)
    by_status = await tz.count_by_transcript_status(ctx.pool)
    oldest_age = await tz.oldest_unprocessed_age_seconds(ctx.pool)

    lines = ["منابع:"]
    for source, count in sorted(by_source.items()):
        lines.append(f"  {source}: {count}")
    lines.append("رونویسی:")
    for status, count in sorted(by_status.items()):
        lines.append(f"  {status}: {count}")
    if oldest_age is not None:
        lines.append(f"قدیمی‌ترین مورد پردازش‌نشده: {int(oldest_age // 60)} دقیقه پیش")
    else:
        lines.append("همه موارد پردازش شده‌اند.")

    await ctx.provider.send_message(msg.chat_id, "\n".join(lines))


async def _items(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    async with ctx.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT type, title, deadline FROM items ORDER BY created_at DESC LIMIT %s",
            (ITEMS_LIMIT,),
        )
        rows = await cur.fetchall()

    if not rows:
        await ctx.provider.send_message(msg.chat_id, NO_ITEMS_TEXT)
        return

    lines = [f"آخرین {len(rows)} آیتم:"]
    for item_type, title, deadline in rows:
        label = _TYPE_LABEL.get(item_type, f"• {item_type}")
        line = f"{label} — {title}"
        if deadline is not None:
            line += f" (تا {deadline.isoformat()})"
        lines.append(line)

    await ctx.provider.send_message(msg.chat_id, "\n".join(lines))
