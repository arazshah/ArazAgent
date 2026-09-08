from __future__ import annotations

from app.logsafe import redact

# Built by concatenation, not as a contiguous literal, so this test file
# itself doesn't trip scripts/check_secrets.sh while still exercising the
# real redact() behavior on the assembled (fake) secret-shaped string.
_FAKE_SK_KEY = "sk-" + "abcdefghijklmnopqrstuvwxyz"
_FAKE_BEARER_TOKEN = "abcdefghijklmnopqrstuvwxyz"


def test_redacts_sk_style_key():
    assert "sk-" not in redact(f"using key {_FAKE_SK_KEY}")


def test_redacts_bearer_header():
    text = redact(f"Authorization: Bearer {_FAKE_BEARER_TOKEN}")
    assert _FAKE_BEARER_TOKEN not in text


def test_redacts_bale_bot_url():
    text = redact("calling https://tapi.bale.ai/bot123456:ABCDEFGHIJ/getMe")
    assert "123456:ABCDEFGHIJ" not in text


def test_leaves_normal_text_alone():
    assert redact("hello world, update_id=42") == "hello world, update_id=42"
