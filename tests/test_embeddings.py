"""Phase 4 semantic search: embed_text() is mocked (no live network). Covers
app.embeddings' contract: embedding failure never raises, missing config or
a failed call just leaves items.embedding NULL, and search degrades to an
empty list rather than crashing.
"""

from __future__ import annotations

from app import embeddings
from app.settings_store import SettingsStore


async def _insert_item(pool, title: str = "buy milk", status: str = "open") -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO items (type, title, decision, status) "
            "VALUES ('note', %s, 'auto', %s) RETURNING id",
            (title, status),
        )
        (item_id,) = await cur.fetchone()
    return item_id


async def _embedding(pool, item_id: int):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT embedding::text FROM items WHERE id = %s", (item_id,))
        (value,) = await cur.fetchone()
    return value


async def _configure_llm(settings: SettingsStore) -> None:
    await settings.set("llm.base_url", "https://api.avalai.ir/v1")
    await settings.set("llm.api_key", "test-key")
    await settings.set("llm.embedding_model", "text-embedding-3-small")


async def test_embed_item_disabled_leaves_embedding_null(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await settings.set("llm.embedding_enabled", "false")
    item_id = await _insert_item(pool)

    await embeddings.embed_item(pool, settings, item_id, "buy milk")

    assert await _embedding(pool, item_id) is None


async def test_embed_item_not_configured_leaves_embedding_null(pool, crypto):
    settings = SettingsStore(pool, crypto)
    item_id = await _insert_item(pool)

    await embeddings.embed_item(pool, settings, item_id, "buy milk")

    assert await _embedding(pool, item_id) is None


async def test_embed_item_call_failure_leaves_embedding_null(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    item_id = await _insert_item(pool)

    async def fake_embed_text(base_url, api_key, model, text):
        return None

    monkeypatch.setattr(embeddings, "embed_text", fake_embed_text)

    await embeddings.embed_item(pool, settings, item_id, "buy milk")

    assert await _embedding(pool, item_id) is None


async def test_embed_item_success_stores_vector(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    item_id = await _insert_item(pool)

    async def fake_embed_text(base_url, api_key, model, text):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embeddings, "embed_text", fake_embed_text)

    await embeddings.embed_item(pool, settings, item_id, "buy milk")

    stored = await _embedding(pool, item_id)
    assert stored is not None


async def test_search_items_returns_empty_when_not_configured(pool, crypto):
    settings = SettingsStore(pool, crypto)

    results = await embeddings.search_items(pool, settings, "milk")

    assert results == []


async def test_search_items_ranks_nearest_first(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)

    close_id = await _insert_item(pool, "buy milk")
    far_id = await _insert_item(pool, "plan vacation")

    vectors = {
        "buy milk": [1.0, 0.0, 0.0],
        "plan vacation": [0.0, 1.0, 0.0],
        "milk please": [0.9, 0.1, 0.0],
    }

    async def fake_embed_text(base_url, api_key, model, text):
        return vectors[text]

    monkeypatch.setattr(embeddings, "embed_text", fake_embed_text)

    await embeddings.embed_item(pool, settings, close_id, "buy milk")
    await embeddings.embed_item(pool, settings, far_id, "plan vacation")

    results = await embeddings.search_items(pool, settings, "milk please")

    assert [r[0] for r in results] == [close_id, far_id]


async def test_search_items_ignores_unembedded_rows(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    await _insert_item(pool, "never embedded")

    async def fake_embed_text(base_url, api_key, model, text):
        return [1.0, 0.0]

    monkeypatch.setattr(embeddings, "embed_text", fake_embed_text)

    results = await embeddings.search_items(pool, settings, "anything")

    assert results == []


async def test_find_similar_open_item_flags_a_close_neighbor(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    original_id = await _insert_item(pool, "buy milk")
    duplicate_id = await _insert_item(pool, "buy some milk")

    vectors = {"buy milk": [1.0, 0.0, 0.0], "buy some milk": [0.99, 0.01, 0.0]}

    async def fake_embed_text(base_url, api_key, model, text):
        return vectors[text]

    monkeypatch.setattr(embeddings, "embed_text", fake_embed_text)
    await embeddings.embed_item(pool, settings, original_id, "buy milk")
    await embeddings.embed_item(pool, settings, duplicate_id, "buy some milk")

    result = await embeddings.find_similar_open_item(pool, duplicate_id)

    assert result is not None
    other_id, title, distance = result
    assert other_id == original_id
    assert title == "buy milk"
    assert distance < embeddings.DEFAULT_DUPLICATE_THRESHOLD


async def test_find_similar_open_item_ignores_unrelated_items(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    milk_id = await _insert_item(pool, "buy milk")
    vacation_id = await _insert_item(pool, "plan vacation")

    vectors = {"buy milk": [1.0, 0.0, 0.0], "plan vacation": [0.0, 1.0, 0.0]}

    async def fake_embed_text(base_url, api_key, model, text):
        return vectors[text]

    monkeypatch.setattr(embeddings, "embed_text", fake_embed_text)
    await embeddings.embed_item(pool, settings, milk_id, "buy milk")
    await embeddings.embed_item(pool, settings, vacation_id, "plan vacation")

    assert await embeddings.find_similar_open_item(pool, vacation_id) is None


async def test_find_similar_open_item_ignores_done_items(pool, crypto, monkeypatch):
    settings = SettingsStore(pool, crypto)
    await _configure_llm(settings)
    closed_id = await _insert_item(pool, "buy milk", status="done")
    new_id = await _insert_item(pool, "buy some milk")

    vectors = {"buy milk": [1.0, 0.0, 0.0], "buy some milk": [0.99, 0.01, 0.0]}

    async def fake_embed_text(base_url, api_key, model, text):
        return vectors[text]

    monkeypatch.setattr(embeddings, "embed_text", fake_embed_text)
    await embeddings.embed_item(pool, settings, closed_id, "buy milk")
    await embeddings.embed_item(pool, settings, new_id, "buy some milk")

    assert await embeddings.find_similar_open_item(pool, new_id) is None


async def test_find_similar_open_item_returns_none_without_own_embedding(pool, crypto):
    item_id = await _insert_item(pool, "buy milk")

    assert await embeddings.find_similar_open_item(pool, item_id) is None
