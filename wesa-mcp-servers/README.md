# WESA MCP Servers

Three MCP servers that give WESA agents real tool access — file system, shell commands, and browser automation. Each runs in its own Docker container and communicates over StreamableHTTP transport.

---

## Servers

| Server | Port | Purpose |
|---|---|---|
| `file_server` | 8001 | Read, write, list, and delete files in the workspace |
| `shell_server` | 8002 | Run whitelisted shell commands against the workspace |
| `browser_server` | 8003 | Playwright-powered browser automation (Chromium, headless) |

---

## Prerequisites

- Docker Desktop running
- `WESA_PROJECT_ROOT` set to the absolute path of `agent-ecosystem/` on the host

The `.env` file in this directory already sets the correct path for Greg's machine:
```
WESA_PROJECT_ROOT=C:\Users\wujek\WesaTeam\agent-ecosystem
```

If running on a different machine, update `.env` before starting.

---

## Running

### Start all three servers
```powershell
cd WesaTeam\agent-ecosystem\wesa-mcp-servers
docker-compose up
```

### Start in background (detached)
```powershell
docker-compose up -d
```

### Stop
```powershell
docker-compose down
```

### Rebuild after code changes
```powershell
docker-compose build --no-cache
docker-compose up
```

### Rebuild a single server only
```powershell
docker-compose build --no-cache browser_server
docker-compose up
```

---

## Verifying servers are healthy

Once running, each server exposes a `/health` endpoint:

```powershell
curl http://localhost:8001/health   # file_server
curl http://localhost:8002/health   # shell_server
curl http://localhost:8003/health   # browser_server
```

Expected response from all three:
```json
{"status": "ok"}
```

### Inspect tools via MCP Inspector
```bash
npx @modelcontextprotocol/inspector http://localhost:8001/mcp
npx @modelcontextprotocol/inspector http://localhost:8002/mcp
npx @modelcontextprotocol/inspector http://localhost:8003/mcp
```

---

## Tool Reference

### file_server (port 8001)

MCP endpoint: `http://localhost:8001/mcp`

| Tool | Arguments | Description |
|---|---|---|
| `read_file` | `path: str` | Read file contents. Path relative to `FILE_SERVER_ROOT`. |
| `write_file` | `path: str`, `content: str` | Write content to file. Creates parent dirs if needed. |
| `list_files` | `path: str`, `pattern: str (optional)` | List directory or glob matches. |
| `file_exists` | `path: str` | Returns `true` or `false`. |
| `delete_file` | `path: str` | Delete a file. |

All paths are scoped to the mounted workspace (`FILE_SERVER_ROOT=/workspace`). Path traversal attempts are blocked.

---

### shell_server (port 8002)

MCP endpoint: `http://localhost:8002/mcp`

| Tool | Arguments | Description |
|---|---|---|
| `run_command` | `command: str`, `args: list[str]`, `cwd: str (optional)` | Run a whitelisted command. Returns `exit_code`, `stdout`, `stderr`. |
| `get_allowlist` | none | Returns current list of allowed commands. |

**Security rules:**
- Only commands in `shell_config.json` allowlist are permitted
- `cwd` must be under `/workspace` — no escaping the workspace
- `sudo` in any argument is blocked
- 30-second timeout on all commands

**Default allowlist:** `python`, `python3`, `pip`, `pytest`, `ruff`, `git`, `npm`, `npx`, `node`, `ls`, `cat`, `grep`, `find`, `diff`, `docker`, `docker-compose`, and more.

To add or remove commands, edit `shell_server/shell_config.json` and restart the container.

---

### browser_server (port 8003)

MCP endpoint: `http://localhost:8003/mcp`

Single Playwright Chromium session persists across all tool calls within a task.

| Tool | Arguments | Description |
|---|---|---|
| `navigate` | `url: str` | Navigate to URL. |
| `get_text` | none | Get visible text of current page. |
| `get_html` | none | Get full HTML source of current page. |
| `click` | `selector: str` | Click element by CSS selector. |
| `fill` | `selector: str`, `value: str` | Fill input field by CSS selector. |
| `screenshot` | `path: str (optional)` | Save screenshot to path, or return base64 PNG if no path. |
| `evaluate` | `script: str` | Run JavaScript and return result as string. |
| `reset_browser` | none | Close current context, open a fresh one. |

---

## Configuration

### Workspace root
`WESA_PROJECT_ROOT` in `.env` controls what gets mounted into `/workspace` inside the containers. Set it to the `agent-ecosystem/` directory on the host.

### Shell allowlist
`shell_server/shell_config.json` — edit and restart `shell_server` to apply changes.

### Ports
Ports `8001`, `8002`, `8003` must be free on the host. If they conflict, change the left-hand port in `docker-compose.yml`:
```yaml
ports:
  - "9001:8001"   # host:container
```
Then update `SERVER_REGISTRY` in `src/tool_layer.py` to match.

---

## Architecture Notes

- Transport: **StreamableHTTP** — production-ready, Docker-compatible, SDK/npm targeting ready
- Each server is a standalone Python app using [FastMCP](https://github.com/modelcontextprotocol/python-sdk)
- MCP protocol endpoint is at `/{server}/mcp` path on each server
- `restart: unless-stopped` in docker-compose — servers recover automatically from crashes
- `daemon.py` is unchanged — tool access is wired in Phase 2 via `src/mcp_client.py` and Phase 3 via `src/tool_layer.py`

---

## What's Next (Phase 2 + 3)

Phase 1 (this directory) is complete. The next two phases wire these servers into the WESA agent runtime:

**Phase 2 — `src/mcp_client.py`**: Universal MCPClient class. Connects to server URLs, discovers tools, executes them, runs the multi-turn agent loop.

**Phase 3 — `src/tool_layer.py`**: URL resolver + health checker. Agents declare `mcp_servers` in their YAML. `router.py` calls `tool_layer.resolve()` and passes server configs to the agent. Zero changes to `daemon.py`.
