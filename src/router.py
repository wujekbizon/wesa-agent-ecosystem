"""
WESA Agent Ecosystem - Task Router
Loads agent skills from YAML files and routes each task to the right specialist.

Routing:
  task.type → skill lookup → correct model (Gemini or Ollama) with specialist system prompt
"""

from src.task import Task
from src.gemini_agent import GeminiAgent
from src.ollama_agent import OllamaAgent
from src.agent_loader import load_skills, AgentSkill


class Router:
    def __init__(self):
        self.skills = load_skills()
        self._agents: dict[str, GeminiAgent | OllamaAgent] = {}
        self._log_loaded_skills()

    def _log_loaded_skills(self):
        print("[Router] Loaded agents:")
        seen = set()
        for task_type, skill in self.skills.items():
            if skill.name not in seen:
                print(f"  - {skill.name} ({skill.role}) → {skill.model} | handles: {skill.task_types}")
                seen.add(skill.name)

    def _get_agent(self, skill: AgentSkill) -> GeminiAgent | OllamaAgent:
        if skill.name not in self._agents:
            if skill.model == "ollama":
                self._agents[skill.name] = OllamaAgent(
                    system_prompt=skill.system_prompt,
                    agent_name=skill.name,
                )
            else:
                self._agents[skill.name] = GeminiAgent(
                    system_prompt=skill.system_prompt,
                    agent_name=skill.name,
                )
        return self._agents[skill.name]

    def run(self, task: Task) -> tuple[str, str]:
        """Returns (result, model_used_string)"""
        skill = self.skills.get(task.type)

        if not skill:
            # Fallback: use ResearchAgent for unknown types
            skill = self.skills.get("general")
            if not skill:
                raise RuntimeError(f"No agent found for task type '{task.type}' and no fallback available.")

        agent = self._get_agent(skill)
        result = agent.run(task.prompt)
        return result, agent.model_name
