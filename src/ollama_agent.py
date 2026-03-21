"""
WESA Agent Ecosystem - Ollama Agent
Handles: code generation and review (stays local - no data leaves machine).
Model: qwen3.5:35b
System prompt is injected per-agent from skill YAML files.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SYSTEM_PROMPT = """You are a WESA developer agent. Write clean, production-quality code.
Follow best practices. Return only code and brief explanations."""

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:35b")


class OllamaAgent:
    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT, agent_name: str = "OllamaAgent"):
        self.host = OLLAMA_HOST
        self._model = OLLAMA_MODEL
        self.system_prompt = system_prompt
        self.agent_name = agent_name

    def run(self, prompt: str) -> str:
        try:
            response = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self._model,
                    "system": self.system_prompt,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                },
                timeout=300,
            )
            response.raise_for_status()
            return response.json()["response"].strip()
        except requests.exceptions.ConnectionError:
            raise RuntimeError("Ollama is not running. Start with: ollama serve")
        except Exception as e:
            raise RuntimeError(f"{self.agent_name} error: {e}")

    @property
    def model_name(self) -> str:
        return f"{self._model} ({self.agent_name})"
