"""
WESA Agent Ecosystem - Task Router
Decides which agent handles which task type.

Routing logic:
- CODE tasks → Ollama (local, private, no data leaves machine)
- Everything else → Gemini (fast, broad knowledge, cheap)
"""

from src.task import Task, TaskType
from src.gemini_agent import GeminiAgent
from src.ollama_agent import OllamaAgent


class Router:
    def __init__(self):
        self._gemini = None
        self._ollama = None

    @property
    def gemini(self) -> GeminiAgent:
        if not self._gemini:
            self._gemini = GeminiAgent()
        return self._gemini

    @property
    def ollama(self) -> OllamaAgent:
        if not self._ollama:
            self._ollama = OllamaAgent()
        return self._ollama

    def run(self, task: Task) -> tuple[str, str]:
        """
        Returns (result, model_used)
        """
        if task.type == TaskType.CODE:
            return self.ollama.run(task.prompt), self.ollama.model_name
        else:
            return self.gemini.run(task.prompt), self.gemini.model_name
