"""Shared Persian display labels for item type/decision — used by
app.commands (bot replies), app.admin.routes (admin item browser), and
app.triage (the immediate decision announcement), kept in one place
instead of three near-identical dicts.
"""

from __future__ import annotations

TYPE_LABELS = {
    "task": "📌 کار",
    "note": "📝 یادداشت",
    "idea": "💡 ایده",
    "event": "📅 رویداد",
}

DECISION_LABELS = {
    "do_now": "🟢 همین حالا",
    "schedule": "🟡 زمان‌بندی",
    "delegate": "🔵 بسپار",
    "archive": "⚪ بایگانی",
    "decline": "🔴 رد شد",
}
