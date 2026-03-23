"""
WESA Agent Ecosystem - Agent Loader
Loads agent skill definitions from agents/skills/*.yaml
Each skill file defines an agent's name, model, task types, and system prompt.
Validates all skill files on load — invalid files are skipped, not fatal.
"""

import logging
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "agents" / "skills"
ALLOWED_MODELS = {"gemini", "ollama"}
MIN_PROMPT_LENGTH = 50


@dataclass
class AgentSkill:
    name: str
    role: str
    model: str
    task_types: list[str]
    system_prompt: str
    use_web_search: bool = False
    gemini_model: str = ""  # Optional per-agent Gemini model override (e.g. "gemini-3.1-pro-preview")


def validate_skill(data: dict[str, Any], filepath: str) -> tuple[bool, str]:
    """Validates a skill dict against the WESA schema. Returns (is_valid, error_message)."""
    if not isinstance(data, dict):
        return False, f"Expected object at root of {filepath}"

    errors = []

    if "name" not in data:
        errors.append("'name' is missing")
    elif not isinstance(data["name"], str) or not data["name"].strip():
        errors.append("'name' must be a non-empty string")

    if "role" not in data:
        errors.append("'role' is missing")
    elif not isinstance(data["role"], str) or not data["role"].strip():
        errors.append("'role' must be a non-empty string")

    if "model" not in data:
        errors.append("'model' is missing")
    elif not isinstance(data["model"], str):
        errors.append("'model' must be a string")
    elif data["model"].lower() not in ALLOWED_MODELS:
        errors.append(f"'model' must be one of {ALLOWED_MODELS}, got '{data.get('model')}'")

    if "task_types" not in data:
        errors.append("'task_types' is missing")
    elif not isinstance(data["task_types"], list) or len(data["task_types"]) < 1:
        errors.append("'task_types' must be a non-empty list")
    else:
        for i, item in enumerate(data["task_types"]):
            if not isinstance(item, str):
                errors.append(f"'task_types[{i}]' must be a string")

    if "system_prompt" not in data:
        errors.append("'system_prompt' is missing")
    elif not isinstance(data["system_prompt"], str):
        errors.append("'system_prompt' must be a string")
    elif len(data["system_prompt"]) < MIN_PROMPT_LENGTH:
        errors.append(f"'system_prompt' must be at least {MIN_PROMPT_LENGTH} chars")

    if errors:
        return False, f"Validation failed for {filepath}: {'; '.join(errors)}"
    return True, ""


def load_skills(skills_dir: Path = SKILLS_DIR) -> dict[str, AgentSkill]:
    """
    Load all skill YAML files from skills_dir.
    Returns dict keyed by task_type -> AgentSkill.
    Invalid files are skipped with a logged warning.
    """
    skills: dict[str, AgentSkill] = {}
    loaded = 0
    failed = 0

    for path in sorted(skills_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"[AgentLoader] Failed to parse {path.name}: {e}")
            failed += 1
            continue

        if not data:
            logger.warning(f"[AgentLoader] Empty file skipped: {path.name}")
            failed += 1
            continue

        is_valid, error_msg = validate_skill(data, path.name)
        if not is_valid:
            logger.error(f"[AgentLoader] {error_msg}")
            failed += 1
            continue

        skill = AgentSkill(
            name=data["name"],
            role=data["role"],
            model=data["model"].lower(),
            task_types=data["task_types"],
            system_prompt=data["system_prompt"].strip(),
            use_web_search=bool(data.get("use_web_search", False)),
            gemini_model=str(data.get("gemini_model", "")),
        )
        for task_type in skill.task_types:
            skills[task_type] = skill
        loaded += 1

    logger.info(f"[AgentLoader] {loaded} skills loaded, {failed} failed validation.")
    return skills


def get_skill_for_task(task_type: str, skills: dict[str, AgentSkill]) -> AgentSkill | None:
    return skills.get(task_type)
