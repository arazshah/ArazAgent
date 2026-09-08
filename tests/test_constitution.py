"""The constitution (goals/hard-rules/capacity) that turns triage into a
gatekeeper — see app/constitution.py's module docstring. Pure parsing is
tested directly; remaining_capacity_hours and build_constitution_context
against real rows (no LLM, no network).
"""

from __future__ import annotations

from app import constitution
from app.settings_store import SettingsStore


def test_parse_goals_empty_or_none_returns_empty_list():
    assert constitution.parse_goals(None) == []
    assert constitution.parse_goals("") == []
    assert constitution.parse_goals("   ") == []


def test_parse_goals_single_goal():
    assert constitution.parse_goals("مرجع GeoAI فارسی:5") == [("مرجع GeoAI فارسی", 5)]


def test_parse_goals_multiple_goals_separated_by_semicolon():
    result = constitution.parse_goals("هدف یک:3; هدف دو:5;هدف سه:1")
    assert result == [("هدف یک", 3), ("هدف دو", 5), ("هدف سه", 1)]


def test_parse_goals_missing_weight_defaults_to_three():
    assert constitution.parse_goals("بدون وزن") == [("بدون وزن", 3)]


def test_parse_goals_malformed_weight_defaults_to_three():
    assert constitution.parse_goals("هدف:notanumber") == [("هدف", 3)]


def test_parse_goals_skips_empty_chunks():
    assert constitution.parse_goals("هدف یک:3;;  ;هدف دو:2") == [("هدف یک", 3), ("هدف دو", 2)]


def test_parse_goals_skips_entries_with_empty_title():
    assert constitution.parse_goals(":5; هدف واقعی:2") == [("هدف واقعی", 2)]


async def _insert_open_task(pool, effort_minutes: int | None) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO items (type, title, decision, status, effort_minutes) "
            "VALUES ('task', 'x', 'auto', 'open', %s)",
            (effort_minutes,),
        )


async def test_remaining_capacity_with_no_open_tasks_equals_full_capacity(pool, crypto):
    remaining = await constitution.remaining_capacity_hours(pool, 40)
    assert remaining == 40


async def test_remaining_capacity_subtracts_open_task_effort(pool, crypto):
    await _insert_open_task(pool, effort_minutes=120)  # 2 hours
    await _insert_open_task(pool, effort_minutes=60)  # 1 hour

    remaining = await constitution.remaining_capacity_hours(pool, 40)

    assert remaining == 37


async def test_remaining_capacity_ignores_done_tasks(pool, crypto):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO items (type, title, decision, status, effort_minutes) "
            "VALUES ('task', 'x', 'auto', 'done', 600)"
        )

    remaining = await constitution.remaining_capacity_hours(pool, 40)

    assert remaining == 40


async def test_remaining_capacity_can_go_negative(pool, crypto):
    await _insert_open_task(pool, effort_minutes=60 * 50)  # 50 hours of open work

    remaining = await constitution.remaining_capacity_hours(pool, 40)

    assert remaining == -10


async def test_build_constitution_context_with_nothing_configured(pool, crypto):
    settings = SettingsStore(pool, crypto)

    context = await constitution.build_constitution_context(pool, settings)

    assert context["goals"] == []
    assert context["hard_rules"] == ""
    assert context["weekly_capacity_hours"] == constitution.DEFAULT_WEEKLY_CAPACITY_HOURS
    assert context["remaining_capacity_hours"] == constitution.DEFAULT_WEEKLY_CAPACITY_HOURS


async def test_build_constitution_context_reads_configured_values(pool, crypto):
    settings = SettingsStore(pool, crypto)
    await settings.set("constitution.goals", "هدف من:4")
    await settings.set("constitution.hard_rules", "هیچ پروژه‌ای زیر فلان نرخ")
    await settings.set("constitution.weekly_capacity_hours", "20")
    await _insert_open_task(pool, effort_minutes=60)

    context = await constitution.build_constitution_context(pool, settings)

    assert context["goals"] == [("هدف من", 4)]
    assert context["hard_rules"] == "هیچ پروژه‌ای زیر فلان نرخ"
    assert context["weekly_capacity_hours"] == 20
    assert context["remaining_capacity_hours"] == 19
