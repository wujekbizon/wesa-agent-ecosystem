# MCP Inspector Guide

MCP Inspector is an interactive browser UI for testing MCP servers manually — connect to any server, browse its tools, call them with custom arguments, and inspect responses. No code required.

---

## Prerequisites

Node.js must be installed. Verify with:
```powershell
node --version
```

---

## Starting the Inspector

MCP Inspector runs via `npx` — no installation needed.

### Connect to file_server
```powershell
npx @modelcontextprotocol/inspector http://localhost:8001/mcp
```

### Connect to shell_server
```powershell
npx @modelcontextprotocol/inspector http://localhost:8002/mcp
```

### Connect to browser_server
```powershell
npx @modelcontextprotocol/inspector http://localhost:8003/mcp
```

The Inspector opens automatically in your browser at `http://localhost:6274`.

> The WESA MCP servers must be running (`docker-compose up`) before connecting.

---

## Using the Inspector

### 1. Set transport type — CRITICAL

**Inspector v0.21+ defaults to STDIO transport.** WESA servers use Streamable HTTP. If you see a "Command" field or get an `ENOENT` error, the wrong transport is selected.

To fix:
1. Find the **Transport Type** dropdown (top of the connection panel)
2. Change it from `STDIO` → **`Streamable HTTP`**
3. A **URL** field will appear — enter the server URL (e.g. `http://localhost:8001/mcp`)
4. Click **Connect**

The URL passed on the command line may not override this — always verify the dropdown is set to `Streamable HTTP` before connecting.

### 2. Connect
On success you'll see: `Connected` and the server name (e.g. `FileServer`, `shell-server`, `wesa-browser-server`).

### 3. Browse tools
Click the **Tools** tab. You'll see all tools the server exposes with their names and descriptions.

Click any tool to expand it and see its input schema.

### 4. Call a tool
Select a tool, fill in the arguments in the form fields, and click **Run Tool**.

The response appears in the right panel — either the return value or an error message.

---

## Test Calls — file_server (port 8001)

**List files in workspace root:**
```
Tool: list_files
path: ""
pattern: (leave empty)
```

**List only Python files recursively:**
```
Tool: list_files
path: ""
pattern: **/*.py
```

**Read a specific file:**
```
Tool: read_file
path: src/router.py
```

**Check if a file exists:**
```
Tool: file_exists
path: daemon.py
```

**Write a test file:**
```
Tool: write_file
path: axel_test.txt
content: hello from MCP
```

**Delete the test file:**
```
Tool: delete_file
path: axel_test.txt
```

---

## Test Calls — shell_server (port 8002)

**See what commands are allowed:**
```
Tool: get_allowlist
(no arguments)
```

**List workspace root:**
```
Tool: run_command
command: ls
args: []
cwd: /workspace
```

**Run Python version check:**
```
Tool: run_command
command: python3
args: ["--version"]
```

**Run ruff on the source folder:**
```
Tool: run_command
command: ruff
args: ["check", "src/"]
cwd: /workspace
```

**Run pytest:**
```
Tool: run_command
command: pytest
args: ["tests/", "-v"]
cwd: /workspace
```

---

## Test Calls — browser_server (port 8003)

**Navigate to a URL:**
```
Tool: navigate
url: https://example.com
```

**Get visible text of the page:**
```
Tool: get_text
(no arguments)
```

**Get full HTML:**
```
Tool: get_html
(no arguments)
```

**Take a screenshot (returns base64):**
```
Tool: screenshot
path: (leave empty)
```

**Run JavaScript on the page:**
```
Tool: evaluate
script: document.title
```

**Reset browser to a clean state:**
```
Tool: reset_browser
(no arguments)
```

---

## What to Look For

| Result | Meaning |
|---|---|
| Tool returns expected value | Server and tool are working correctly |
| `Path escape attempt blocked` | Path traversal guard is working |
| `Command not allowed` | Shell allowlist is enforced |
| `Browser not initialised` | browser_server startup failed — check `docker-compose logs browser_server` |
| `AttributeError` or `500` | Bug in server code — check container logs |

---

## Checking Container Logs

If a tool call fails unexpectedly, check the server logs:

```powershell
# All servers
docker-compose logs

# Single server only
docker-compose logs file_server
docker-compose logs shell_server
docker-compose logs browser_server

# Follow logs in real time
docker-compose logs -f browser_server
```

---

## Stopping the Inspector

Press `Ctrl+C` in the terminal where `npx` is running.

The WESA MCP servers keep running — Inspector is a separate process.
