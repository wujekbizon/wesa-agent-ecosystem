import sys
import os
import json
import sqlite3
import signal
import subprocess
import asyncio
import shutil
from pathlib import Path
from typing import Optional, Any, Dict, List

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# --- Path & Environment Setup ---
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

# Import ecosystem components
try:
    from src.task_queue import TaskQueue
except ImportError as e:
    raise RuntimeError(f"Failed to import TaskQueue: {e}")

# --- Configuration ---
PORT = 8765
DB_PATH = BASE_DIR / "logs" / "tasks.db"
LOGS_DIR = BASE_DIR / "logs"
TASKS_DIR = BASE_DIR / "tasks"
STATIC_DIR = BASE_DIR / "static"
DASHBOARD_FILE = STATIC_DIR / "dashboard.html"
PID_FILE = LOGS_DIR / "daemon.pid"
DAEMON_SCRIPT = BASE_DIR / "daemon.py"

# --- Initialization ---
# Ensure directories exist
LOGS_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Initialize Task Queue
queue = TaskQueue(db_path=str(DB_PATH))

app = FastAPI(
    title="WESA Web Monitor",
    description="Local dashboard for WESA Agent Ecosystem",
    version="1.0.0"
)

# --- Static Files ---
# Mount static directory for general assets, but we handle dashboard.html specifically
# to serve the main index without a trailing slash in the root route.
if not STATIC_DIR.is_dir():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- Endpoints ---

@app.get("/")
async def root():
    """Serve the dashboard HTML file."""
    if not DASHBOARD_FILE.exists():
        # Fallback if file doesn't exist (though requirement implies it should)
        return FileResponse(STATIC_DIR / "404.html", status_code=404)
    return FileResponse(str(DASHBOARD_FILE))

@app.get("/api/tasks")
async def get_tasks():
    """Return list of all tasks from SQLite."""
    # get_all() returns list[dict] based on requirements
    return queue.get_all()

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Return task dict merged with report content."""
    task_data = queue.get_all()
    task: Optional[Dict[str, Any]] = None
    
    for t in task_data:
        if t.get("id") == task_id:
            task = t
            break
            
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Merge report content
    report_path = LOGS_DIR / f"{task_id}_report.md"
    report_content = ""
    if report_path.exists():
        try:
            report_content = report_path.read_text(encoding="utf-8")
        except Exception:
            report_content = "" # Fail silently on read errors

    task["report"] = report_content
    return task

@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete task from DB and corresponding JSON file."""
    # (a) Delete from SQLite
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Task not found in database")
        conn.commit()
    finally:
        conn.close()

    # (b) Delete tasks/<task_id>.json if it exists
    json_file = TASKS_DIR / f"{task_id}.json"
    if json_file.exists():
        try:
            json_file.unlink()
        except PermissionError:
            # Log error but don't fail the whole request if file lock prevents deletion
            pass

    return {"ok": True}

@app.get("/api/daemon/status")
async def daemon_status():
    """Check if daemon is running based on PID file."""
    pid = None
    if PID_FILE.exists():
        try:
            content = PID_FILE.read_text(encoding="utf-8").strip()
            pid = int(content)
            # Check if process exists
            os.kill(pid, 0)
            return {"running": True, "pid": pid}
        except (ValueError, ProcessLookupError, OSError):
            return {"running": False, "pid": pid}
    
    return {"running": False, "pid": None}

@app.post("/api/daemon/restart")
async def daemon_restart():
    """Restart the daemon process."""
    # 1. Kill existing process
    pid_to_kill = None
    if PID_FILE.exists():
        try:
            content = PID_FILE.read_text(encoding="utf-8").strip()
            pid_to_kill = int(content)
            os.kill(pid_to_kill, signal.SIGTERM)
        except (ValueError, ProcessLookupError, OSError):
            pass # Process might have already died

    # 2. Wait
    await asyncio.sleep(1.5)

    # 3. Start new subprocess
    proc = subprocess.Popen(
        [sys.executable, "daemon.py"],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # 4. Update PID file
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
    except PermissionError:
        pass # Log error in production, but return success as process started

    return {"ok": True, "pid": proc.pid}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket connection for real-time task updates."""
    await websocket.accept()
    
    # Send current state immediately
    await websocket.send_json(queue.get_all())

    try:
        while True:
            # Send updated list every 2 seconds
            await websocket.send_json(queue.get_all())
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        # Graceful disconnect handling
        pass

# --- Main Entry Point ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web_monitor:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info"
    )