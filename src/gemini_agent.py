"""
WESA Agent Ecosystem - Gemini Agent

** REVIVED 2026-06-20 ** (was dead 2026-06-13 -> 2026-06-20, E0.3 audit)
Gemini free tier expired 2026-05-07; Greg provisioned a new GOOGLE_API_KEY on
2026-06-20 (3-month free trial, EXPIRES 2026-09-20). Key lives in .env (this
module reads it via os.getenv("GOOGLE_API_KEY") below — there is no
vault-injection wiring yet, that's E1 provider-abstraction work) and is
mirrored in secure_core vault (status=active, rotate_by=2026-09-20) for
audit/redaction coverage. src.router constructs this class normally again
for any skill with model='gemini' — see router.py _get_agent().

ACTION BY 2026-09-20: renew/replace the key or this whole fleet (R1, W1, UX1,
FE1, PM1, Q1, SU1, T1, A1, EX1) goes dark again. See the 2026-06-14 Gemini
replacement options report (_plans/) if migrating off Google instead.

Still relevant context from the dead period, kept for the provider rewrite:
  - this is a working reference implementation of the agent.run() contract
    that OllamaAgent and the future OpenAICompatProvider/ClaudeCodeProvider
    (E1) also implement.
  - `google-genai` lives in the optional `gemini-legacy` extra in
    pyproject.toml rather than core deps — still true, not undone by reviving
    the key. Reinstall that extra if a fresh environment errors on import.

---
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
from src.mcp_client import MCPClient, MCPTool

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
        timeout_override: int | None = None,
    ):
        self._timeout = timeout_override if timeout_override else int(os.getenv("GEMINI_TIMEOUT", "120"))
        self.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        
        if model_override:
            self._model_name = model_override
        else:
            self._model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.use_web_search = use_web_search

    def run(self, prompt: str, server_configs: list[dict] | None = None) -> str:
        MAX_TURNS = 20 if self.agent_name in ("r1", "R1") else 10

        def _call_no_tools() -> str:
            config_args = {"system_instruction": self.system_prompt}
            if self.use_web_search:
                config_args["tools"] = [types.Tool(google_search=types.GoogleSearch())]
            response = self.client.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**config_args),
            )
            if response.text is None:
                raise RuntimeError("Gemini returned no text content (possible safety block or invalid model name)")
            return response.text.strip()

        def _call_with_tools() -> str:
            mcp = MCPClient()
            mcp.connect(server_configs)
            mcp_tools = mcp.discover_tools()

            if not mcp_tools:
                mcp.close()
                return _call_no_tools()

            func_decls = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters=t.input_schema if t.input_schema else {},
                )
                for t in mcp_tools
            ]
            config = types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=[types.Tool(function_declarations=func_decls)],
            )
            contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
            result_text = ""

            for turn in range(MAX_TURNS):
                response = self.client.models.generate_content(
                    model=self._model_name,
                    contents=contents,
                    config=config,
                )
                candidate = response.candidates[0] if response.candidates else None
                if candidate is None:
                    break

                function_calls = [
                    p.function_call
                    for p in candidate.content.parts
                    if p.function_call is not None
                ]

                if not function_calls:
                    result_text = response.text.strip() if response.text else ""
                    break

                contents.append(candidate.content)

                tool_response_parts = []
                for fc in function_calls:
                    tool_result = mcp.call_tool(fc.name, dict(fc.args) if fc.args else {})
                    tool_response_parts.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=fc.name,
                                response={"result": tool_result},
                            )
                        )
                    )
                contents.append(types.Content(role="tool", parts=tool_response_parts))

                if turn == MAX_TURNS - 1:
                    raise RuntimeError(f"{self.agent_name} hit MAX_TURNS ({MAX_TURNS}) — research incomplete")

            mcp.close()
            return result_text

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                if server_configs:
                    future = executor.submit(_call_with_tools)
                else:
                    future = executor.submit(_call_no_tools)
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
        timeout_override: int | None = None,
    ) -> "GeminiAgent":
        return cls(
            system_prompt=system_prompt,
            agent_name=agent_name,
            use_web_search=True,
            model_override=model_override,
            timeout_override=timeout_override,
        )

    @property
    def model_name(self) -> str:
        return f"{self._model_name} ({self.agent_name})"