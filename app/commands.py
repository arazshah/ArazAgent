"""Bot commands: /start /today /stats /items /tasks /done /search /review
/commitments. No admin commands over the bot — all configuration happens
in the web UI, so a compromised messaging account can never change
settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import tz
from app.decisions_log import apply_status_change
from app.embeddings import search_items
from app.jalali import format_deadline
from app.labels import DECISION_LABELS, TYPE_LABELS
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
    "/commitments برای دیدن تعهدهای باز به دیگران\n"
    "/search عبارت برای جست‌وجوی معنایی در آیتم‌ها\n"
    "/review برای مرور دوره‌ای"
)

UNKNOWN_COMMAND_TEXT = "دستور ناشناخته."

NO_ITEMS_TEXT = "هنوز هیچ آیتمی دسته‌بندی نشده است."
NO_OPEN_TASKS_TEXT = "کار بازی وجود ندارد."
NO_OPEN_COMMITMENTS_TEXT = "تعهد بازی به کسی ثبت نشده است."
DONE_USAGE_TEXT = "استفاده: /done شماره (مثلاً /done 5)"
SEARCH_USAGE_TEXT = "استفاده: /search عبارت جست‌وجو"
NO_SEARCH_RESULTS_TEXT = "چیزی پیدا نشد."

ITEMS_LIMIT = 10
TASKS_LIMIT = 20
COMMITMENTS_LIMIT = 20


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
    elif command == "commitments":
        await _commitments(msg, ctx)
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
            "SELECT type, title, deadline, decision, score FROM items "
            "ORDER BY created_at DESC LIMIT %s",
            (ITEMS_LIMIT,),
        )
        rows = await cur.fetchall()

    if not rows:
        await ctx.provider.send_message(msg.chat_id, NO_ITEMS_TEXT)
        return

    lines = [f"آخرین {len(rows)} آیتم:"]
    for item_type, title, deadline, decision, score in rows:
        label = TYPE_LABELS.get(item_type, f"• {item_type}")
        decision_label = DECISION_LABELS.get(decision, "")
        line = f"{label} {decision_label} — {title}"
        if score is not None:
            line += f" (امتیاز {score})"
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


async def _commitments(msg: IncomingMessage, ctx: CaptureContext) -> None:
    """Commitments to other people are the same `items` rows as everything
    else, just tagged with commitment_to (see app/triage.py) — this is a
    dedicated view, not a separate table, specifically so they're never
    buried in the general task list: breaking a promise to someone else
    costs trust in a way a missed personal task doesn't.
    """
    assert msg.chat_id is not None
    async with ctx.pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, title, deadline, commitment_to FROM items
            WHERE status = 'open' AND commitment_to IS NOT NULL
            ORDER BY deadline NULLS LAST, created_at
            LIMIT %s
            """,
            (COMMITMENTS_LIMIT,),
        )
        rows = await cur.fetchall()

    if not rows:
        await ctx.provider.send_message(msg.chat_id, NO_OPEN_COMMITMENTS_TEXT)
        return

    lines = ["تعهدهای باز به دیگران:"]
    for item_id, title, deadline, commitment_to in rows:
        line = f"#{item_id} 🤝 {commitment_to} — {title}"
        if deadline is not None:
            line += f" (تا {format_deadline(deadline)})"
        lines.append(line)
    lines.append("\nبرای بستن یک مورد: /done شماره")

    await ctx.provider.send_message(msg.chat_id, "\n".join(lines))


async def _mark_done(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    assert msg.text is not None
    parts = msg.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await ctx.provider.send_message(msg.chat_id, DONE_USAGE_TEXT)
        return

    item_id = int(parts[1])
    result = await apply_status_change(ctx.pool, item_id, "done", only_if_status_differs=True)

    if result is None:
        await ctx.provider.send_message(
            msg.chat_id, f"آیتم #{item_id} پیدا نشد یا قبلاً بسته شده است."
        )
        return

    title, _decision, _score = result
    await ctx.provider.send_message(msg.chat_id, f"✅ بسته شد: {title}")


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
        label = TYPE_LABELS.get(item_type, f"• {item_type}")
        line = f"#{item_id} {label} — {title}"
        if deadline is not None:
            line += f" (تا {format_deadline(deadline)})"
        lines.append(line)

    await ctx.provider.send_message(msg.chat_id, "\n".join(lines))


async def _review(msg: IncomingMessage, ctx: CaptureContext) -> None:
    assert msg.chat_id is not None
    text = await build_review_text(ctx.pool)
    await ctx.provider.send_message(msg.chat_id, text)
