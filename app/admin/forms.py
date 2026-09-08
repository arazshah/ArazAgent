"""Per-group field definitions and validators for the settings page.

Kept intentionally simple (plain dataclasses, not full pydantic models):
each group is a short, fixed list of fields and the validation rules are a
handful of one-liners. A generic form framework would be overkill here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse


class FieldValidationError(ValueError):
    def __init__(self, field_key: str, message: str) -> None:
        super().__init__(message)
        self.field_key = field_key
        self.message = message


def validate_int_csv(value: str) -> str:
    if not value.strip():
        return ""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    for p in parts:
        if not p.lstrip("-").isdigit():
            raise ValueError("باید فهرستی از اعداد صحیح جدا شده با ویرگول باشد")
    return ",".join(parts)


def validate_mode(value: str) -> str:
    if value not in ("webhook", "polling"):
        raise ValueError("باید webhook یا polling باشد")
    return value


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("آدرس معتبر نیست")
    return value


def validate_positive_int(value: str) -> str:
    try:
        n = int(value)
    except ValueError as exc:
        raise ValueError("باید یک عدد صحیح مثبت باشد") from exc
    if n <= 0:
        raise ValueError("باید یک عدد صحیح مثبت باشد")
    return str(n)


def validate_nonempty(value: str) -> str:
    if not value.strip():
        raise ValueError("این فیلد نمی‌تواند خالی باشد")
    return value


def validate_bool(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned not in ("true", "false"):
        raise ValueError("باید true یا false باشد")
    return cleaned


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    is_secret: bool
    validate: Callable[[str], str] = staticmethod(lambda v: v)


GROUPS: dict[str, list[FieldSpec]] = {
    "bale": [
        FieldSpec("bale.bot_token", "توکن ربات بله", True, validate_nonempty),
        FieldSpec("bale.allowed_user_ids", "شناسه‌های کاربری مجاز", False, validate_int_csv),
        FieldSpec("bale.mode", "حالت دریافت پیام", False, validate_mode),
        FieldSpec("bale.webhook_secret", "کلید مخفی وبهوک", True, validate_nonempty),
        FieldSpec("bale.public_base_url", "آدرس عمومی سرویس", False, validate_url),
    ],
    "llm": [
        FieldSpec("llm.provider", "ارائه‌دهنده", False, validate_nonempty),
        FieldSpec("llm.base_url", "آدرس پایه", False, validate_url),
        FieldSpec("llm.api_key", "کلید API", True, validate_nonempty),
        FieldSpec("llm.chat_model", "مدل گفتگو", False, validate_nonempty),
        FieldSpec(
            "llm.triage_enabled", "دسته‌بندی خودکار فعال باشد (true/false)", False, validate_bool
        ),
    ],
    "transcription": [
        FieldSpec("transcription.backend", "بک‌اند رونویسی", False, validate_nonempty),
        FieldSpec("transcription.model", "مدل رونویسی", False, validate_nonempty),
        FieldSpec("transcription.local_model", "مدل محلی", False, validate_nonempty),
        FieldSpec("transcription.language", "زبان", False, validate_nonempty),
    ],
    "system": [
        FieldSpec("system.kill_switch_target", "هدف تعداد روزانه", False, validate_positive_int),
    ],
}
