"""Phase 4: best-effort semantic embeddings for `items` rows, backing
/search. Same contract as triage and transcription: never raises, and a
failure just leaves items.embedding NULL — app.recovery retries it later,
and the item still exists and is fully usable everywhere else (/items,
/tasks, /done) in the meantime.
"""

from __future__ import annotations

import logging

from psycopg_pool import AsyncConnectionPool

from app.llm import embed_text
from app.settings_store import SettingsStore

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 5

# pgvector cosine distance: 0 = identical, 2 = opposite. Chosen empirically
# for short Persian task/note titles rather than derived from anything —
# tight enough that unrelated items essentially never trigger it, loose
# enough to catch the same idea worded slightly differently.
DEFAULT_DUPLICATE_THRESHOLD = 0.15


def _to_vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


async def _embedding_settings(settings: SettingsStore) -> tuple[str, str, str] | None:
    base_url = await settings.get("llm.base_url")
    api_key = await settings.get("llm.api_key")
    model = await settings.get("llm.embedding_model")
    if not base_url or not api_key or not model:
        return None
    return base_url, api_key, model


async def embed_item(
    pool: AsyncConnectionPool, settings: SettingsStore, item_id: int, text: str
) -> None:
    enabled = (await settings.get("llm.embedding_enabled")) or "true"
    if enabled.strip().lower() == "false":
        return
    if not text or not text.strip():
        return

    configured = await _embedding_settings(settings)
    if configured is None:
        logger.warning(
            "embedding skipped for item_id=%s: llm.base_url/api_key/embedding_model not configured",
            item_id,
        )
        return
    base_url, api_key, model = configured

    embedding = await embed_text(base_url, api_key, model, text)
    if embedding is None:
        return  # embed_text already logged the failure; retried by recovery

    try:
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE items SET embedding = %s::vector WHERE id = %s",
                (_to_vector_literal(embedding), item_id),
            )
    except Exception as exc:  # noqa: BLE001 - e.g. a dimension mismatch after
        # switching llm.embedding_model on a table with older embeddings —
        # degrade, never crash the caller.
        logger.warning("failed to store embedding for item_id=%s: %s", item_id, exc)


async def search_items(
    pool: AsyncConnectionPool,
    settings: SettingsStore,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[tuple]:
    """Returns [(id, title, type, deadline), ...] nearest-first. Empty on
    any failure (LLM down, nothing embedded yet, dimension mismatch) — the
    caller treats that the same as "no matches".
    """
    configured = await _embedding_settings(settings)
    if configured is None:
        return []
    base_url, api_key, model = configured

    embedding = await embed_text(base_url, api_key, model, query)
    if embedding is None:
        return []

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, title, type, deadline
                FROM items
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (_to_vector_literal(embedding), limit),
            )
            return await cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the caller
        logger.warning("semantic search query failed: %s", exc)
        return []


async def find_similar_open_item(
    pool: AsyncConnectionPool,
    item_id: int,
    threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
) -> tuple[int, str, float] | None:
    """Nearest other open item to `item_id`'s own embedding, if within
    `threshold` cosine distance — the duplicate-capture check triage.py
    runs right after embedding a freshly saved item. Comparing entirely in
    SQL (a self-join on items.id, never pulling the raw vector into
    Python) sidesteps needing a pgvector type adapter registered on this
    connection. Returns None if item_id has no embedding yet (best-effort
    embedding failed or is still pending), no open item is close enough,
    or the query itself fails — degrade, never block the capture over
    this.
    """
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT other.id, other.title,
                       other.embedding <=> this.embedding AS distance
                FROM items AS this, items AS other
                WHERE this.id = %s
                  AND this.embedding IS NOT NULL
                  AND other.id != this.id
                  AND other.status = 'open'
                  AND other.embedding IS NOT NULL
                ORDER BY distance
                LIMIT 1
                """,
                (item_id,),
            )
            row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - degrade, never block the capture
        logger.warning("duplicate check failed for item_id=%s: %s", item_id, exc)
        return None

    if row is None:
        return None
    other_id, title, distance = row
    if distance > threshold:
        return None
    return other_id, title, distance
