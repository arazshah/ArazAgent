"""Phase 2: turns one captured inbox row's text into a classified `items`
row via a single LLM call (app.llm.classify_capture). Same contract as
transcription (app/transcribe/orchestrator.py): never raises, and any
failure leaves inbox.processed_at unset so the row stays in the
`inbox_unprocessed` queue for app.recovery to retry later — never drop.

Scores and gatekeeps against app.constitution's goals/hard-rules/capacity
rather than just labeling — see ARCHITECTURE.md's "Constitution-driven
scoring" section.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import date

from psycopg_pool import AsyncConnectionPool

from app.constitution import build_constitution_context
from app.embeddings import embed_item
from app.labels import DECISION_LABELS, TYPE_LABELS
from app.llm import VALID_DECISIONS, VALID_ITEM_TYPES, classify_capture
from app.settings_store import SettingsStore

NotifyFn = Callable[[str], Awaitable[None]]

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 200
DEFAULT_DECISION = "schedule"
MIN_SCORE, MAX_SCORE = 0, 25


async def _mark_processed(pool: AsyncConnectionPool, inbox_id: int) -> None:
    async with pool.connection() as conn:
        await conn.execute("UPDATE inbox SET processed_at = now() WHERE id = %s", (inbox_id,))


def _clean_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _clean_effort_minutes(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        n = int(value.strip())
        return n if n > 0 else None
    return None


def _clean_deadline(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _clean_score(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        n = int(value.strip())
    else:
        return None
    return max(MIN_SCORE, min(MAX_SCORE, n))


def _clean_decision(value: object) -> str:
    return value if value in VALID_DECISIONS else DEFAULT_DECISION


def _apply_capacity_guard(
    decision: str, decision_reason: str | None, remaining_capacity_hours: float
) -> tuple[str, str | None, bool]:
    """The LLM is prompted to never answer "do_now" once capacity is spent,
    but a prompt is a request, not a guarantee — models drift. This is the
    backstop: if capacity is at or below zero, "do_now" is downgraded to
    "schedule" here in code, every time, regardless of what the model said.
    Any other decision (schedule/delegate/archive/decline) is left alone —
    none of those claim time this week, so none of them need capping.
    """
    if decision != "do_now" or remaining_capacity_hours > 0:
        return decision, decision_reason, False
    note = "ظرفیت این هفته پر است؛ به «زمان‌بندی» تغییر یافت."
    combined_reason = f"{decision_reason} — {note}" if decision_reason else note
    return DEFAULT_DECISION, combined_reason, True


def _require_trade_off_for_do_now(
    decision: str, decision_reason: str | None, what_to_drop: str | None
) -> tuple[str, str | None, bool]:
    """Every "do it now" is a new commitment accepted onto an already-full
    plate — so the prompt requires "what_to_drop_instead" whenever decision
    is "do_now". This is the backstop for when the model answers "do_now"
    anyway without naming a trade-off: without this, every "yes" only ever
    grows the list, never shrinks it. Downgrade to "schedule" here, in code,
    rather than trust the model to have honored the prompt's requirement.
    """
    if decision != "do_now" or what_to_drop:
        return decision, decision_reason, False
    note = "چیزی برای کنار گذاشتن مشخص نشد؛ به «زمان‌بندی» تغییر یافت."
    combined_reason = f"{decision_reason} — {note}" if decision_reason else note
    return DEFAULT_DECISION, combined_reason, True


def _format_decision_announcement(
    item_id: int,
    item_type: str,
    title: str,
    decision: str,
    score: int | None,
    decision_reason: str | None,
    what_to_drop: str | None,
    commitment_to: str | None,
) -> str:
    type_label = TYPE_LABELS.get(item_type, item_type)
    decision_label = DECISION_LABELS.get(decision, decision)
    lines = [f"{type_label} {decision_label} — {title} (#{item_id})"]
    if score is not None:
        lines.append(f"امتیاز: {score}/25")
    if decision_reason:
        lines.append(f"دلیل: {decision_reason}")
    if what_to_drop:
        lines.append(f"به‌جاش کنار بذار: {what_to_drop}")
    if commitment_to:
        lines.append(f"🤝 تعهد به: {commitment_to}")
    return "\n".join(lines)


async def triage_inbox_row(
    pool: AsyncConnectionPool,
    settings: SettingsStore,
    inbox_id: int,
    text: str | None,
    notify: NotifyFn | None = None,
) -> None:
    enabled = (await settings.get("llm.triage_enabled")) or "true"
    if enabled.strip().lower() == "false":
        return

    if not text or not text.strip():
        # Nothing to classify (e.g. a document with no caption) — there is
        # no item to create, so the row counts as handled.
        await _mark_processed(pool, inbox_id)
        return

    base_url = await settings.get("llm.base_url")
    api_key = await settings.get("llm.api_key")
    model = await settings.get("llm.chat_model")
    if not base_url or not api_key or not model:
        logger.warning(
            "triage skipped for inbox_id=%s: llm.base_url/api_key/chat_model not configured",
            inbox_id,
        )
        return  # leave unprocessed; recovery retries once configured

    constitution = await build_constitution_context(pool, settings)
    result = await classify_capture(base_url, api_key, model, text, constitution)
    if "error" in result:
        logger.warning("triage failed for inbox_id=%s: %s", inbox_id, result["error"])
        return  # leave unprocessed; recovery retries

    item_type = result.get("type")
    if item_type not in VALID_ITEM_TYPES:
        item_type = "note"
    title = (_clean_text(result.get("title")) or text.strip())[:MAX_TITLE_LENGTH]
    what_to_drop = _clean_text(result.get("what_to_drop_instead"))
    meta: dict[str, object] = {"what_to_drop_instead": what_to_drop} if what_to_drop else {}
    score = _clean_score(result.get("score"))
    decision = _clean_decision(result.get("decision"))
    decision_reason = _clean_text(result.get("decision_reason"))
    decision, decision_reason, missing_trade_off = _require_trade_off_for_do_now(
        decision, decision_reason, what_to_drop
    )
    if missing_trade_off:
        meta["missing_trade_off"] = True
    decision, decision_reason, capacity_capped = _apply_capacity_guard(
        decision, decision_reason, constitution["remaining_capacity_hours"]
    )
    if capacity_capped:
        meta["capacity_capped"] = True
    commitment_to = _clean_text(result.get("commitment_to"))

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO items (
                inbox_id, type, title, project, goal_key, effort_minutes,
                deadline, score, decision, decision_reason, meta, commitment_to
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
                inbox_id,
                item_type,
                title,
                _clean_text(result.get("project")),
                _clean_text(result.get("goal_key")),
                _clean_effort_minutes(result.get("effort_minutes")),
                _clean_deadline(result.get("deadline")),
                score,
                decision,
                decision_reason,
                json.dumps(meta),
                commitment_to,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        item_id = row[0]
        await conn.execute("UPDATE inbox SET processed_at = now() WHERE id = %s", (inbox_id,))

    # Phase 4: best-effort — a failure here never undoes the item above.
    await embed_item(pool, settings, item_id, text)

    if notify is not None:
        announcement = _format_decision_announcement(
            item_id, item_type, title, decision, score, decision_reason, what_to_drop, commitment_to
        )
        try:
            await notify(announcement)
        except Exception:  # noqa: BLE001 - the item is already saved; never lose it over this
            logger.exception("failed to send decision announcement for item_id=%s", item_id)
