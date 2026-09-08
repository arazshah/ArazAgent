"""Persian (Jalali/Shamsi) calendar — presentation and LLM-prompt-context
only. Storage stays Gregorian: `items.deadline` is a plain SQL `date` and
nothing about that changes here, so there is no ambiguity in the database
or in app.triage's date parsing. This module only:

  1. tells the triage LLM today's date in both calendars, so it can
     resolve a Shamsi or relative date expression ("۱۵ مهر", "دوشنبه‌ی
     بعد") the user actually wrote, while still producing the Gregorian
     YYYY-MM-DD app.triage._clean_deadline() expects; and
  2. formats a stored Gregorian deadline back into Shamsi for anything a
     human reads (bot replies, the admin item browser) — Persian dates in
     a Persian-language product.
"""

from __future__ import annotations

from datetime import date, datetime

import jdatetime

from app.tz import TEHRAN

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

_MONTH_NAMES = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)


def to_persian_digits(value: str) -> str:
    return value.translate(_PERSIAN_DIGITS)


def _format(j: jdatetime.date) -> str:
    day = to_persian_digits(str(j.day))
    year = to_persian_digits(str(j.year))
    return f"{day} {_MONTH_NAMES[j.month - 1]} {year}"


def today_jalali_str() -> str:
    """Today's Shamsi date (Tehran time) as a display string, e.g. '۱۵ دی ۱۴۰۴'."""
    return _format(jdatetime.date.fromgregorian(date=datetime.now(TEHRAN).date()))


def format_deadline(value: date | str | None) -> str | None:
    """Format a stored Gregorian deadline (a date, an ISO string, or None)
    as a Shamsi display string. None in, None out — templates and bot
    replies already branch on "is there a deadline at all" before calling
    this.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return _format(jdatetime.date.fromgregorian(date=value))
