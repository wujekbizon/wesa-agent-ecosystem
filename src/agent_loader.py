"""
WESA Agent Ecosystem - Agent Loader
Loads agent skill definitions from agents/skills/*.yaml
Each skill file defines an agent's name, model, task types, and system prompt.
"""

import yaml
from pathlib import Path
from dataclasses import dataclass


SKILLS_DIR = Path(__file__).parent.parent / "agents" / "skills"


@dataclass
class AgentSkill:
    name: str
    role: str
    model: str          # "gemini" or "ollama"
    task_types: list[str]
    system_prompt: str


def load_skills() -> dict[str, AgentSkill]:
    """Load all skill YAML files. Returns dict keyed by task_type."""
    skills: dict[str, AgentSkill] = {}

    for path in SKILLS_DIR.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            skill = AgentSkill(
                name=data["name"],
                role=data["role"],
                model=data["model"],
                task_types=data["task_types"],
                system_prompt=data["system_prompt"].strip(),
            )
            for task_type in skill.task_types:
                skills[task_type] = skill
        except Exception as e:
            print(f"[AgentLoader] Failed to load {path.name}: {e}")

    return skills


def get_skill_for_task(task_type: str, skills: dict[str, AgentSkill]) -> AgentSkill | None:
    return skills.get(task_type)
