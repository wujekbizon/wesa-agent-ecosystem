"""
WESA Agent Ecosystem - Gemini Agent
Handles: research, writing, analysis, general tasks
Model: gemini-2.5-flash (fast, cheap, broad knowledge)
"""

import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are a WESA worker agent. You execute tasks efficiently and return clean,
structured results. Be concise. No unnecessary filler. Output only what was asked for."""


class GeminiAgent:
    def __init__(self):
        self.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        self._model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    def run(self, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                ),
            )
            return response.text.strip()
        except Exception as e:
            raise RuntimeError(f"Gemini error: {e}")

    @property
    def model_name(self) -> str:
        return self._model_name
