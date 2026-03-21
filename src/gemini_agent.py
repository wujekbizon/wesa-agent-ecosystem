"""
WESA Agent Ecosystem - Gemini Agent
Handles: research, analysis, writing, QA review tasks.
Model: gemini-2.5-flash
System prompt is injected per-agent from skill YAML files.
"""

import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SYSTEM_PROMPT = """You are a WESA worker agent. Execute tasks efficiently.
Return clean, structured results. Be concise. No unnecessary filler."""


class GeminiAgent:
    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT, agent_name: str = "GeminiAgent"):
        self.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        self._model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.system_prompt = system_prompt
        self.agent_name = agent_name

    def run(self, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_prompt,
                ),
            )
            return response.text.strip()
        except Exception as e:
            raise RuntimeError(f"{self.agent_name} error: {e}")

    @property
    def model_name(self) -> str:
        return f"{self._model_name} ({self.agent_name})"
