import json
import os
import subprocess
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Route

# Load configuration at module startup
CONFIG_PATH = os.getenv("SHELL_CONFIG_PATH", "/app/config.json")
ALLOWLIST: list[str] = ["echo"]
TIMEOUT_SECONDS: int = 30
WORKSPACE_ROOT: str = "/workspace"
MCP_BIND_HOST = os.getenv("WESA_MCP_BIND_HOST", "127.0.0.1")
EXEC_ENABLED = os.getenv("WESA_MCP_ALLOW_EXEC", "false").lower() == "true"


def load_config() -> None:
    global ALLOWLIST, TIMEOUT_SECONDS, WORKSPACE_ROOT
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        ALLOWLIST = config.get("allowlist", ["echo"])
        TIMEOUT_SECONDS = config.get("timeout_seconds", 30)
        WORKSPACE_ROOT = config.get("workspace_root", "/workspace")
    except FileNotFoundError:
        pass  # Use safe defaults


load_config()

# Initialize FastMCP
mcp = FastMCP("shell-server")


def _require_exec_enabled() -> None:
    if not EXEC_ENABLED:
        raise PermissionError("Shell execution disabled by P0 containment")


@mcp.tool()
def run_command(command: str, args: list[str], cwd: str = "/workspace") -> str:
    _require_exec_enabled()
    # Security check a: command must be in allowlist
    if command not in ALLOWLIST:
        raise ValueError(f"Command not allowed: {command}")

    # Security check b: cwd must be under /workspace
    cwd_path = Path(cwd).resolve()
    workspace_path = Path(WORKSPACE_ROOT).resolve()
    try:
        cwd_path.relative_to(workspace_path)
    except ValueError:
        raise ValueError(f"cwd must be under /workspace, got: {cwd}")

    # Security check c: no argument may contain 'sudo'
    for arg in args:
        if "sudo" in arg:
            raise ValueError("sudo not permitted")

    # Execute command
    try:
        result = subprocess.run(
            [command] + args, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd=cwd
        )
        return f"exit_code: {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    except subprocess.TimeoutExpired:
        return "exit_code: -1\nstdout:\n\nstderr:\nCommand timed out"
    except Exception as e:
        return f"exit_code: -1\nstdout:\n\nstderr:\n{str(e)}"


@mcp.tool()
def get_allowlist() -> str:
    return "\n".join(ALLOWLIST)


def health_check(request):
    return JSONResponse({"status": "ok"})


app = mcp.streamable_http_app()
app.routes.insert(0, Route("/health", health_check))


if __name__ == "__main__":
    uvicorn.run(app, host=MCP_BIND_HOST, port=8002)
