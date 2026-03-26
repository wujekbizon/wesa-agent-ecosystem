"""
WESA Agent Ecosystem - Task Router
Loads agent skills from YAML files and routes each task to the right specialist.

Routing:
  task.type → skill lookup → correct model (Gemini or Ollama) with specialist system prompt
  - Handles 'manage' and 'coordinate' via PM1 agent (loaded via YAML)
  - Supports dynamic web search configuration for Research agent (R1)
"""

import logging
import dataclasses

from src.task import Task
from src.gemini_agent import GeminiAgent
from src.ollama_agent import OllamaAgent
from src.agent_loader import load_skills, AgentSkill
from src import tool_layer

logger = logging.getLogger(__name__)


class Router:
    def __init__(self):
        self.skills = load_skills()
        self._agents: dict[str, GeminiAgent | OllamaAgent] = {}
        self._log_loaded_skills()

    @property
    def loaded_agent_names(self) -> list[str]:
        """Returns a sorted list of unique agent names currently loaded."""
        return sorted({skill.name for skill in self.skills.values() if hasattr(skill, 'name')})

    def _log_loaded_skills(self):
        print('[Router] Loaded agents:')
        seen = set()
        for task_type, skill in self.skills.items():
            if skill.name not in seen:
                search_flag = getattr(skill, 'use_web_search', False)
                print(f'  - {skill.name} ({skill.role}) -> {skill.model} | handles: {skill.task_types} | search: {search_flag}')
                seen.add(skill.name)

    def _get_agent(self, skill: AgentSkill) -> GeminiAgent | OllamaAgent:
        use_web_search = getattr(skill, 'use_web_search', False)
        model_override = getattr(skill, 'gemini_model', '') or ''
        _t = getattr(skill, 'gemini_timeout', 0)
        timeout_override = int(_t) if _t else None

        if skill.model == 'ollama':
            self._agents[skill.name] = OllamaAgent(
                system_prompt=skill.system_prompt,
                agent_name=skill.name,
                model_override=skill.ollama_model or None,
                ollama_options=skill.ollama_options or None,
            )
        else:
            if use_web_search:
                self._agents[skill.name] = GeminiAgent.with_search(
                    system_prompt=skill.system_prompt,
                    agent_name=skill.name,
                    model_override=model_override or None,
                    timeout_override=timeout_override,
                )
            else:
                self._agents[skill.name] = GeminiAgent(
                    system_prompt=skill.system_prompt,
                    agent_name=skill.name,
                    model_override=model_override or None,
                    timeout_override=timeout_override,
                )
        return self._agents[skill.name]

    def run(self, task: Task) -> tuple[str, str]:
        """Returns (result, model_used_string)"""
        skill = self.skills.get(task.type)

        if not skill:
            # Attempt fallback to 'general' skill
            fallback_skill = self.skills.get('general')

            if fallback_skill:
                skill = fallback_skill
            else:
                # Improved logging for missing fallback
                logger.warning(
                    f"Router: No agent found for task type '{task.type}' (ID: {task.id}). "
                    f"No 'general' fallback configured. Task dropped."
                )
                raise RuntimeError(
                    f"No agent found for task type '{task.type}' and no fallback available."
                )

        agent = self._get_agent(skill)
        server_configs = tool_layer.resolve(dataclasses.asdict(skill))
        result = agent.run(task.prompt, server_configs=server_configs)
        return result, agent.model_name