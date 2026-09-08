"""Bot commands: /start /today /stats /items /tasks /done /search /review.
No admin commands over the bot — all configuration happens in the web UI,
so a compromised messaging account can never change settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import tz
from app.embeddings import search_items
from app.jalali import format_deadline
from app.providers.base import IncomingMessage
from app.review import build_review_text

if TYPE_CHECKING:
    from app.capture import CaptureContext

START_TEXT = (
    "لایه ثبت فعال است.\n\n"
    "هر چیزی به ذهنت آمد بفرست — متن، ویس، فوروارد، لینک.\n"
    "دسته‌بندی نکن. توضیح نده. فقط بفرست.\n\n"
    "/today برای شمارش\n"
    "/items برای دیدن آخرین آیتم‌های دسته‌بندی‌شده\n"
    "/tasks برای دیدن کارهای باز\n"
    "/done شماره برای بستن یک کار\n"
    "/search عبارت برای جست‌وجوی معنایی در آیتم‌ها\n"
    "/review برای مرور دوره‌ای"
)

UNKNOWN_COMMAND_TEXT = "دستور ناشناخته."

NO_ITEMS_TEXT = "هنوز هیچ آیتمی دسته‌بندی نشده است."
NO_OPEN_TASKS_TEXT = "کار بازی وجود ندارد."
DONE_USAGE_TEXT = "استفاده: /done شماره (مثلاً /done 5)"
SEARCH_USAGE_TEXT = "استفاده: /search عبارت جست‌وجو"
NO_SEARCH_RESULTS_TEXT = "چیزی پیدا نشد."

ITEMS_LIMIT = 10
TASKS_LIMIT = 20

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
    elif command == "tasks":
        await _tasks(msg, ctx)
    elif command == "done":
        await _mark_done(msg, ctx)
    elif command == "search":
        await _search(msg, ctx)
    elif command == "review":
        await _review(msg, ctx)
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
            line += f" (تا {format_deadline(deadline)})"
        lines.append(line)

    await ctx.provider.send_message(msg.chat_id, "\n".join(lines))


async def _tasks(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    async with ctx.pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, title, deadline FROM items
            WHERE type = 'task' AND status = 'open'
            ORDER BY deadline NULLS LAST, created_at
            LIMIT %s
            """,
            (TASKS_LIMIT,),
        )
        rows = await cur.fetchall()

    if not rows:
        await ctx.provider.send_message(msg.chat_id, NO_OPEN_TASKS_TEXT)
        return

    lines = ["کارهای باز:"]
    for item_id, title, deadline in rows:
        line = f"#{item_id} {title}"
        if deadline is not None:
            line += f" (تا {format_deadline(deadline)})"
        lines.append(line)
    lines.append("\nبرای بستن یک کار: /done شماره")

    await ctx.provider.send_message(msg.chat_id, "\n".join(lines))


async def _mark_done(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    assert msg.text is not None
    parts = msg.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await ctx.provider.send_message(msg.chat_id, DONE_USAGE_TEXT)
        return

    item_id = int(parts[1])
    async with ctx.pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE items SET status = 'done', updated_at = now() "
            "WHERE id = %s AND status != 'done' RETURNING title",
            (item_id,),
        )
        row = await cur.fetchone()

    if row is None:
        await ctx.provider.send_message(
            msg.chat_id, f"آیتم #{item_id} پیدا نشد یا قبلاً بسته شده است."
        )
        return

    await ctx.provider.send_message(msg.chat_id, f"✅ بسته شد: {row[0]}")


async def _search(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    assert msg.text is not None
    parts = msg.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await ctx.provider.send_message(msg.chat_id, SEARCH_USAGE_TEXT)
        return

    results = await search_items(ctx.pool, ctx.settings, parts[1].strip())
    if not results:
        await ctx.provider.send_message(msg.chat_id, NO_SEARCH_RESULTS_TEXT)
        return

    lines = ["نتایج جست‌وجو:"]
    for item_id, title, item_type, deadline in results:
        label = _TYPE_LABEL.get(item_type, f"• {item_type}")
        line = f"#{item_id} {label} — {title}"
        if deadline is not None:
            line += f" (تا {format_deadline(deadline)})"
        lines.append(line)

    await ctx.provider.send_message(msg.chat_id, "\n".join(lines))


async def _review(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    text = await build_review_text(ctx.pool)
    await ctx.provider.send_message(msg.chat_id, text)
