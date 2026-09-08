"""Persian (Jalali/Shamsi) calendar conversion — pure functions, no
database, no network. Storage stays Gregorian; these are presentation and
LLM-prompt-context helpers only (see app/jalali.py's module docstring).
"""

from __future__ import annotations

from datetime import date

from app.jalali import format_deadline, to_persian_digits, today_jalali_str


def test_to_persian_digits_converts_ascii_digits():
    assert to_persian_digits("2026") == "۲۰۲۶"
    assert to_persian_digits("15") == "۱۵"


def test_format_deadline_none_stays_none():
    assert format_deadline(None) is None


def test_format_deadline_accepts_date_object():
    assert format_deadline(date(2026, 1, 5)) == "۱۵ دی ۱۴۰۴"


def test_format_deadline_accepts_iso_string():
    assert format_deadline("2026-01-05") == "۱۵ دی ۱۴۰۴"


def test_format_deadline_new_year_boundary():
    # Nowruz 1404 fell on 2025-03-21 (Gregorian) — the day before is still
    # the last day of 1403.
    assert format_deadline("2025-03-21") == "۱ فروردین ۱۴۰۴"
    assert format_deadline("2025-03-20") == "۳۰ اسفند ۱۴۰۳"


def test_today_jalali_str_returns_persian_digit_string():
    result = today_jalali_str()
    assert any(ch in result for ch in "۰۱۲۳۴۵۶۷۸۹")
    assert not any(ch in result for ch in "0123456789")  # no leftover ASCII digits
