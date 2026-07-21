"""
WESA Agent Ecosystem - Tool Layer
Resolves MCP server configs for a given agent.
Reads agent config dict, health-checks declared servers, returns reachable server list.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

# Registry of known MCP servers.
# URL can be overridden per-server via environment variables:
#   WESA_FILE_SERVER_URL, WESA_SHELL_SERVER_URL, WESA_BROWSER_SERVER_URL
SERVER_REGISTRY: dict[str, dict] = {
    "file_server": {"url": os.getenv("WESA_FILE_SERVER_URL", "http://localhost:8001")},
    "shell_server": {"url": os.getenv("WESA_SHELL_SERVER_URL", "http://localhost:8002")},
    "browser_server": {"url": os.getenv("WESA_BROWSER_SERVER_URL", "http://localhost:8003")},
}


def resolve(agent_config: dict) -> list[dict]:
    """
    Takes an agent config dict, returns a list of reachable server configs.
    Each entry: {"name": "file_server", "url": "http://localhost:8001"}

    Unreachable servers are excluded with a logged warning.
    Returns empty list if agent has no mcp_servers field or it is empty.
    """
    requested: list[str] = agent_config.get("mcp_servers") or []
    if not requested:
        return []

    agent_name: str = agent_config.get("name", "unknown")
    resolved: list[dict] = []

    for server_name in requested:
        if server_name not in SERVER_REGISTRY:
            logger.warning(
                f"[tool_layer] Unknown server '{server_name}' in agent '{agent_name}' — skipping"
            )
            continue

        url = SERVER_REGISTRY[server_name]["url"]
        if _is_reachable(url):
            resolved.append({"name": server_name, "url": url})
        else:
            logger.warning(
                f"[tool_layer] Server '{server_name}' unreachable at {url} "
                f"— agent '{agent_name}' will run without it"
            )

    return resolved


def _is_reachable(url: str) -> bool:
    """
    Lightweight health check — HTTP GET to /health endpoint.
    Returns True if status 200, False on any error or non-200 response.
    Times out after 2 seconds to avoid blocking the task queue.
    """
    try:
        response = requests.get(f"{url}/health", timeout=2)
        return response.status_code == 200
    except Exception:
        return False
