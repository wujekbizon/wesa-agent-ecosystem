"""
WESA Agent Ecosystem - Ollama Agent
Handles: code generation and review (stays local - no data leaves machine).
Model: qwen3.5:35b
System prompt is injected per-agent from skill YAML files.
"""

import os
import requests
from dotenv import load_dotenv
from src.mcp_client import MCPClient

load_dotenv()

DEFAULT_SYSTEM_PROMPT = """You are a WESA developer agent. Write clean, production-quality code.
Follow best practices. Return only code and brief explanations."""

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
_timeout_env = os.getenv("OLLAMA_TIMEOUT", "0")
OLLAMA_TIMEOUT = None if _timeout_env == "0" else int(_timeout_env)


class OllamaAgent:
    def __init__(
        self,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        agent_name: str = "OllamaAgent",
        model_override: str | None = None,
        ollama_options: dict | None = None,
    ):
        self.host = OLLAMA_HOST
        self._model = model_override or OLLAMA_MODEL
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self._options = ollama_options or {}

    def run(self, prompt: str, server_configs: list[dict] | None = None) -> str:
        MAX_TURNS = 10

        def _call_no_tools() -> str:
            try:
                payload: dict = {
                    "model": self._model,
                    "system": self.system_prompt,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                }
                if self._options:
                    payload["options"] = self._options
                response = requests.post(
                    f"{self.host}/api/generate",
                    json=payload,
                    timeout=OLLAMA_TIMEOUT,
                )
                response.raise_for_status()
                return response.json()["response"].strip()
            except requests.exceptions.ConnectionError:
                raise RuntimeError("Ollama is not running. Start with: ollama serve")
            except Exception as e:
                raise RuntimeError(f"{self.agent_name} error: {e}")

        def _call_with_tools() -> str:
            mcp = MCPClient()
            mcp.connect(server_configs)
            mcp_tools = mcp.discover_tools()

            if not mcp_tools:
                mcp.close()
                return _call_no_tools()

            # Build Ollama tool declarations (OpenAI format)
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema if t.input_schema else {},
                    },
                }
                for t in mcp_tools
            ]

            # Conversation history for /api/chat
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ]

            result_text = ""

            for turn in range(MAX_TURNS):
                payload: dict = {
                    "model": self._model,
                    "messages": messages,
                    "tools": tools,
                    "stream": False,
                }
                if self._options:
                    payload["options"] = self._options

                try:
                    response = requests.post(
                        f"{self.host}/api/chat",
                        json=payload,
                        timeout=OLLAMA_TIMEOUT,
                    )
                    response.raise_for_status()
                except requests.exceptions.ConnectionError:
                    raise RuntimeError("Ollama is not running. Start with: ollama serve")
                except Exception as e:
                    raise RuntimeError(f"{self.agent_name} error: {e}")

                data = response.json()
                message = data.get("message", {})
                tool_calls = message.get("tool_calls", [])

                if not tool_calls:
                    # No tool calls — final response
                    result_text = message.get("content", "").strip()
                    if turn == MAX_TURNS - 1 and not result_text:
                        result_text = "[WARN: MAX_TURNS reached]"
                    break

                # Add assistant message to history
                messages.append({"role": "assistant", "content": message.get("content", ""), "tool_calls": tool_calls})

                # Execute each tool call
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    arguments = fn.get("arguments", {})
                    if isinstance(arguments, str):
                        import json
                        try:
                            arguments = json.loads(arguments)
                        except Exception:
                            arguments = {}
                    tool_result = mcp.call_tool(tool_name, arguments)
                    messages.append({
                        "role": "tool",
                        "content": tool_result,
                    })

                if turn == MAX_TURNS - 1:
                    result_text = "[WARN: MAX_TURNS reached]"

            mcp.close()
            return result_text

        if server_configs:
            return _call_with_tools()
        return _call_no_tools()

    @property
    def model_name(self) -> str:
        return f"{self._model} ({self.agent_name})"