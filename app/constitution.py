"""The "constitution": weighted goals, hard rules, and a rough capacity
budget that turn app/triage.py from a classifier into a gatekeeper. See
ARCHITECTURE.md's "Constitution-driven scoring" section.

Goals live as one delimited settings string (`constitution.goals`, e.g.
"مرجع GeoAI فارسی:3; درآمد ریموت:3") rather than a dedicated table —
deliberately, since a full goals CRUD UI would be overbuilt before the
goals themselves have stabilized. Promoting this to a real table is a
natural future step once they have (see FUTURE.md).

Everything here degrades gracefully: no goals defined, a malformed
weight, settings not configured — none of it raises. An empty
constitution just means triage falls back to general judgment instead of
goal-weighted scoring, which is the correct behavior while goals are
still being figured out.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from app.settings_store import SettingsStore

DEFAULT_WEEKLY_CAPACITY_HOURS = 40
DEFAULT_GOAL_WEIGHT = 3


def parse_goals(raw: str | None) -> list[tuple[str, int]]:
    """'title:weight; title2:weight2' -> [(title, weight), ...]. A missing
    or malformed weight defaults to 3 rather than dropping the goal —
    only a genuinely empty title is skipped.
    """
    if not raw or not raw.strip():
        return []

    goals: list[tuple[str, int]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            title, _, weight_str = chunk.rpartition(":")
            title = title.strip()
            try:
                weight = int(weight_str.strip())
            except ValueError:
                weight = DEFAULT_GOAL_WEIGHT
        else:
            title = chunk
            weight = DEFAULT_GOAL_WEIGHT
        if not title:
            continue
        goals.append((title, weight))
    return goals


async def remaining_capacity_hours(pool: AsyncConnectionPool, weekly_capacity_hours: int) -> float:
    """A rough proxy for "how full is your plate right now": weekly
    capacity minus the total estimated effort of every currently open
    task (not time-windowed — an open task counts against capacity until
    it's closed, not just during the week it was captured).
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT coalesce(sum(effort_minutes), 0) FROM items "
            "WHERE type = 'task' AND status = 'open'"
        )
        row = await cur.fetchone()
    committed_minutes = row[0] if row is not None else 0
    return weekly_capacity_hours - (committed_minutes / 60)


async def build_constitution_context(pool: AsyncConnectionPool, settings: SettingsStore) -> dict:
    """Everything app.llm.classify_capture needs to act as a gatekeeper:
    the goals to score against, hard "no" rules, and how much capacity is
    left.
    """
    goals_raw = await settings.get("constitution.goals")
    hard_rules = (await settings.get("constitution.hard_rules")) or ""

    try:
        weekly_capacity_hours = int(
            (await settings.get("constitution.weekly_capacity_hours"))
            or str(DEFAULT_WEEKLY_CAPACITY_HOURS)
        )
    except ValueError:
        weekly_capacity_hours = DEFAULT_WEEKLY_CAPACITY_HOURS

    return {
        "goals": parse_goals(goals_raw),
        "hard_rules": hard_rules,
        "weekly_capacity_hours": weekly_capacity_hours,
        "remaining_capacity_hours": await remaining_capacity_hours(pool, weekly_capacity_hours),
    }
