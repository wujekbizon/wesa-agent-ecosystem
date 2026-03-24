"""
WESA Agent Ecosystem - Gemini Agent
Handles: research, analysis, writing, QA review tasks.
Model: gemini-2.5-flash

This agent supports optional Google Search grounding for research-intensive tasks.
Pass use_web_search=True to enable dynamic tool-based retrieval via the Google Search API.
When disabled (default), the agent operates in standard mode without external tool calls.
"""
import os
import concurrent.futures
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SYSTEM_PROMPT = """You are a WESA worker agent. Execute tasks efficiently.
Return clean, structured results. Be concise. No unnecessary filler."""


class GeminiAgent:
    def __init__(
        self,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        agent_name: str = "GeminiAgent",
        use_web_search: bool = False,
        model_override: str | None = None,
    ):
        self._timeout = int(os.getenv("GEMINI_TIMEOUT", "120"))
        self.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        
        # If model_override is provided and non-empty, use it.
        # Otherwise, fall back to environment variable or default.
        if model_override:
            self._model_name = model_override
        else:
            self._model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.use_web_search = use_web_search

    def run(self, prompt: str) -> str:
        def _call():
            config_args = {
                "system_instruction": self.system_prompt,
            }
            if self.use_web_search:
                config_args["tools"] = [types.Tool(google_search=types.GoogleSearch())]
            response = self.client.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**config_args),
            )
            return response.text.strip()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call)
                return future.result(timeout=self._timeout)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(f"{self.agent_name} error: timed out after {self._timeout}s")
        except Exception as e:
            raise RuntimeError(f"{self.agent_name} error: {e}")

    @classmethod
    def with_search(
        cls,
        system_prompt: str,
        agent_name: str,
        model_override: str | None = None,
    ) -> "GeminiAgent":
        return cls(
            system_prompt=system_prompt,
            agent_name=agent_name,
            use_web_search=True,
            model_override=model_override,
        )

    @property
    def model_name(self) -> str:
        return f"{self._model_name} ({self.agent_name})"