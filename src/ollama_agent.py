"""
WESA Agent Ecosystem - Ollama Agent
Handles: code generation, code review (stays local - no data leaves machine)
Model: qwen3.5:35b (local)
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are a WESA developer agent. You write clean, production-quality code.
Follow best practices. Add comments where logic is non-obvious. Return only code and brief explanations."""

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:35b")


class OllamaAgent:
    def __init__(self):
        self.host = OLLAMA_HOST
        self.model = OLLAMA_MODEL

    def run(self, prompt: str) -> str:
        try:
            response = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
                    "stream": False,
                },
                timeout=120,
            )
            response.raise_for_status()
            return response.json()["response"].strip()
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Ollama is not running. Start it with: ollama serve"
            )
        except Exception as e:
            raise RuntimeError(f"Ollama error: {e}")

    @property
    def model_name(self) -> str:
        return self.model
