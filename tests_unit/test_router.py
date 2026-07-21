"""Tests for src.router — agent construction + fallback logic.

The E0.3 dead-Gemini fail-fast guard (RuntimeError on model='gemini') was
removed 2026-06-20 when Greg provisioned a new GOOGLE_API_KEY. See router.py
_get_agent() and AXEL_ORCHESTRATION.yaml hard_deadlines (key expires
2026-09-20) for context.
"""

import pytest

from src.agent_loader import AgentSkill
from src.router import Router


def _gemini_skill(name="r1", task_types=None):
    return AgentSkill(
        name=name,
        role="Researcher",
        model="gemini",
        task_types=task_types or ["research"],
        system_prompt="x" * 60,
    )


def _ollama_skill(name="d1", task_types=None):
    return AgentSkill(
        name=name,
        role="Coder",
        model="ollama",
        task_types=task_types or ["code"],
        system_prompt="x" * 60,
    )


def test_router_loads_skills_from_disk():
    router = Router()
    assert "general" in router.skills
    assert len(router.loaded_agent_names) > 0


def test_get_agent_builds_gemini_agent():
    router = Router()
    skill = _gemini_skill()
    agent = router._get_agent(skill)
    assert agent is not None
    assert router._agents[skill.name] is agent


def test_get_agent_builds_ollama_agent():
    router = Router()
    skill = _ollama_skill()
    agent = router._get_agent(skill)
    assert agent is not None
    assert router._agents[skill.name] is agent


def test_run_raises_for_unknown_task_type_with_no_fallback(monkeypatch):
    router = Router()
    monkeypatch.setitem(router.skills, "general", None)
    # Remove the 'general' fallback entirely to exercise the no-fallback path
    del router.skills["general"]

    from src.task import Task, TaskType

    task = Task(prompt="x", type=TaskType.GENERAL)
    task.type = "totally_unknown_type"

    with pytest.raises(RuntimeError):
        router.run(task)
