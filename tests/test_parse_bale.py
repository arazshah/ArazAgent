from __future__ import annotations

import json
from pathlib import Path

from app.providers.bale import BaleClient

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _client() -> BaleClient:
    return BaleClient(token="test-token")


def test_parses_text_update():
    msg = _client().parse_update(_load("update_text.json"))
    assert msg is not None
    assert msg.update_id == 100001
    assert msg.message_id == 501
    assert msg.chat_id == 12345
    assert msg.user_id == 999
    assert msg.text == "buy milk"
    assert msg.kind == "text"
    assert msg.is_forward is False


def test_parses_voice_update():
    msg = _client().parse_update(_load("update_voice.json"))
    assert msg is not None
    assert msg.kind == "voice"
    assert msg.file_id == "AwADBAADbXXXXXXXXXXXXXXXXXXXXXXX"
    assert msg.duration_s == 7
    assert msg.text is None


def test_malformed_update_returns_none_never_raises():
    msg = _client().parse_update(_load("update_malformed.json"))
    assert msg is None


def test_missing_message_key_is_unknown_kind():
    msg = _client().parse_update({"update_id": 1})
    assert msg is not None
    assert msg.kind == "unknown"
    assert msg.chat_id is None


def test_forward_detected():
    raw = _load("update_text.json")
    raw["message"]["forward_date"] = 1717000000
    msg = _client().parse_update(raw)
    assert msg is not None
    assert msg.is_forward is True


def test_caption_used_as_text_for_documents():
    raw = _load("update_text.json")
    del raw["message"]["text"]
    raw["message"]["document"] = {"file_id": "doc123", "file_unique_id": "docuniq"}
    raw["message"]["caption"] = "receipt"
    msg = _client().parse_update(raw)
    assert msg is not None
    assert msg.kind == "document"
    assert msg.text == "receipt"
    assert msg.file_id == "doc123"
