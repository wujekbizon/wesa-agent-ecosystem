import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict
    server_name: str
    server_url: str


class MCPClient:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread: threading.Thread = threading.Thread(
            target=self._loop.run_forever, daemon=True
        )
        self._thread.start()
        self._tools: list[MCPTool] = []
        self._server_configs: list[dict] = []

    def connect(self, server_configs: list[dict]) -> None:
        self._server_configs = server_configs
        self._tools = self._run(self._discover_all(server_configs), timeout=60)

    def discover_tools(self) -> list[MCPTool]:
        return self._tools

    def call_tool(self, name: str, arguments: dict) -> str:
        tool = next((t for t in self._tools if t.name == name), None)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        try:
            result = self._run(self._call_on_server(tool.server_url, name, arguments), timeout=60)
            return result
        except Exception as e:
            return f"ERROR: {name} failed — {e}"

    def close(self) -> None:
        self._tools = []
        self._server_configs = []

    def _run(self, coro, timeout=30) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _discover_all(self, server_configs: list[dict]) -> list[MCPTool]:
        all_tools: list[MCPTool] = []
        for config in server_configs:
            try:
                tools = await self._discover_from_server(config)
                all_tools.extend(tools)
            except Exception as e:
                logger.warning(
                    f"[MCPClient] Failed to discover tools from {config.get('name', 'unknown')}: {e}"
                )
        return all_tools

    async def _discover_from_server(self, config: dict) -> list[MCPTool]:
        url = config["url"] + "/mcp"
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    MCPTool(
                        name=t.name,
                        description=t.description or "",
                        input_schema=t.inputSchema
                        if isinstance(t.inputSchema, dict)
                        else (t.inputSchema.model_dump() if t.inputSchema else {}),
                        server_name=config["name"],
                        server_url=config["url"],
                    )
                    for t in result.tools
                ]

    async def _call_on_server(self, server_url: str, name: str, arguments: dict) -> str:
        url = server_url + "/mcp"
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                if result.content:
                    parts = []
                    for item in result.content:
                        if hasattr(item, "text"):
                            parts.append(item.text)
                        else:
                            parts.append(str(item))
                    return "\n".join(parts)
                return ""
