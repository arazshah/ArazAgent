from __future__ import annotations

import pytest

from app.settings_store import SettingsError, SettingsStore


async def test_default_when_nothing_set(pool, crypto, monkeypatch):
    monkeypatch.delenv("LLM_CHAT_MODEL", raising=False)
    store = SettingsStore(pool, crypto)
    resolved = await store.resolve("llm.chat_model")
    assert resolved.value == "gpt-4o-mini"
    assert resolved.source == "default"


async def test_env_beats_default(pool, crypto, monkeypatch):
    monkeypatch.setenv("LLM_CHAT_MODEL", "from-env-model")
    store = SettingsStore(pool, crypto)
    resolved = await store.resolve("llm.chat_model")
    assert resolved.value == "from-env-model"
    assert resolved.source == "env"


async def test_db_beats_env_and_default(pool, crypto, monkeypatch):
    monkeypatch.setenv("LLM_CHAT_MODEL", "from-env-model")
    store = SettingsStore(pool, crypto)
    await store.set("llm.chat_model", "from-db-model")
    resolved = await store.resolve("llm.chat_model")
    assert resolved.value == "from-db-model"
    assert resolved.source == "db"


async def test_cache_invalidated_on_write(pool, crypto):
    store = SettingsStore(pool, crypto)
    await store.resolve("llm.chat_model")  # populate cache with default
    await store.set("llm.chat_model", "updated")
    resolved = await store.resolve("llm.chat_model")
    assert resolved.value == "updated"


async def test_secret_round_trip_encrypted_at_rest(pool, crypto):
    store = SettingsStore(pool, crypto)
    await store.set("llm.api_key", "test-secret-value-1234")

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT value_plain, value_enc FROM app_settings WHERE key = 'llm.api_key'"
        )
        value_plain, value_enc = await cur.fetchone()
    assert value_plain is None
    assert value_enc is not None
    assert b"secretvaluexyz" not in bytes(value_enc)

    resolved = await store.resolve("llm.api_key")
    assert resolved.value == "test-secret-value-1234"
    assert resolved.masked_hint == "…1234"


async def test_get_group_returns_all_keys_in_group(pool, crypto):
    store = SettingsStore(pool, crypto)
    group = await store.get_group("llm")
    assert set(group.keys()) == {
        "llm.provider",
        "llm.base_url",
        "llm.api_key",
        "llm.chat_model",
    }


async def test_unknown_key_raises(pool, crypto):
    store = SettingsStore(pool, crypto)
    with pytest.raises(SettingsError):
        await store.resolve("not.a.key")


async def test_allowed_user_ids_parses_csv(pool, crypto):
    store = SettingsStore(pool, crypto)
    await store.set("bale.allowed_user_ids", "111, 222,333")
    assert await store.get_allowed_user_ids() == [111, 222, 333]


async def test_allowed_user_ids_empty_is_empty_list(pool, crypto):
    store = SettingsStore(pool, crypto)
    assert await store.get_allowed_user_ids() == []


async def test_allowed_user_ids_malformed_raises(pool, crypto):
    store = SettingsStore(pool, crypto)
    await store.set("bale.allowed_user_ids", "not-a-number")
    with pytest.raises(SettingsError):
        await store.get_allowed_user_ids()


async def test_kill_switch_target_default(pool, crypto):
    store = SettingsStore(pool, crypto)
    assert await store.get_kill_switch_target() == 100


async def test_reload_callback_fires_on_sensitive_key_change(pool, crypto):
    store = SettingsStore(pool, crypto)
    calls = []
    store.set_reload_callback(lambda: calls.append(1))
    await store.set("llm.api_key", "test-new-key-value")
    assert calls == [1]


async def test_reload_callback_not_fired_for_unrelated_key(pool, crypto):
    store = SettingsStore(pool, crypto)
    calls = []
    store.set_reload_callback(lambda: calls.append(1))
    await store.set("system.kill_switch_target", "50")
    assert calls == []


async def test_clear_removes_row_and_falls_back(pool, crypto, monkeypatch):
    monkeypatch.delenv("LLM_CHAT_MODEL", raising=False)
    store = SettingsStore(pool, crypto)
    await store.set("llm.chat_model", "custom")
    await store.clear("llm.chat_model")
    resolved = await store.resolve("llm.chat_model")
    assert resolved.value == "gpt-4o-mini"
    assert resolved.source == "default"
