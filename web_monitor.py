import sys
import os
import json
import uuid
import sqlite3
import signal
import subprocess
import asyncio
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, Dict, List

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
LOGS_DESIGNS_DIR = LOGS_DIR / "designs"
LOGS_RESEARCH_DIR = LOGS_DIR / "research"
LOGS_DEV_DIR = LOGS_DIR / "dev"
TASKS_DIR = BASE_DIR / "tasks"
STATIC_DIR = BASE_DIR / "static"
DESIGNS_DIR = STATIC_DIR / "designs"
DASHBOARD_FILE = STATIC_DIR / "dashboard.html"
PID_FILE = LOGS_DIR / "daemon.pid"
DAEMON_SCRIPT = BASE_DIR / "daemon.py"

# --- Initialization ---
# Ensure directories exist
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DESIGNS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DEV_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
DESIGNS_DIR.mkdir(parents=True, exist_ok=True)

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
app.mount("/designs", StaticFiles(directory=str(DESIGNS_DIR)), name="designs")

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

    # Merge report content — search all log subfolders
    report_content = ""
    for search_dir in [LOGS_DESIGNS_DIR, LOGS_RESEARCH_DIR, LOGS_DEV_DIR, LOGS_DIR]:
        report_path = search_dir / f"{task_id}_report.md"
        if report_path.exists():
            try:
                report_content = report_path.read_text(encoding="utf-8")
            except Exception:
                pass
            break

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

@app.get("/api/designs")
async def list_designs():
    """Return list of available design prototype HTML files."""
    if not DESIGNS_DIR.exists():
        return []
    files = sorted(DESIGNS_DIR.glob("*.html"))
    return [
        {
            "filename": f.name,
            "name": f.stem.replace("-", " ").replace("_", " ").title(),
            "url": f"/designs/{f.name}",
            "size": f.stat().st_size,
        }
        for f in files
    ]

class ApproveRequest(BaseModel):
    filename: str

class RetryRequest(BaseModel):
    filename: str
    feedback: str = ""

@app.post("/api/designs/approve")
async def approve_design(req: ApproveRequest):
    """
    Approve a design concept:
    1. Rename concept file to approved-{stem}.html (keeps all concepts intact)
    2. Queue UX1 polish task (with full HTML content embedded)
    3. Queue FE1 analysis task (runs after polish via sequential daemon)
    4. Queue Q1 review task
    """
    approved_path = DESIGNS_DIR / req.filename
    if not approved_path.exists():
        raise HTTPException(status_code=404, detail=f"Design file '{req.filename}' not found")

    html_content = approved_path.read_text(encoding="utf-8")
    stem = Path(req.filename).stem
    concept_name = stem.replace("-", " ").replace("_", " ").title()

    # Rename to approved-{stem}.html so multiple approvals can coexist
    approved_filename = f"approved-{stem}.html"
    approved_final = DESIGNS_DIR / approved_filename
    approved_final.write_text(html_content, encoding="utf-8")
    # Remove the original concept file to avoid duplicates in the list
    try:
        approved_path.unlink()
    except Exception:
        pass

    # Create UX1 polish task (timestamp ensures it runs first)
    now = datetime.now()
    polish_id = f"ux1-polish-{uuid.uuid4().hex[:6]}"
    polish_task = {
        "prompt": (
            f"You are UX1, a design specialist. Greg (the founder) has approved the '{concept_name}' concept "
            f"and wants it polished to production-ready quality before handing off to FE1 for implementation.\n\n"
            f"YOUR TASK: Take the existing prototype below and refine it. Do not change the core aesthetic direction - "
            f"that has been approved. Instead:\n"
            f"- Fix any visual inconsistencies or rough edges\n"
            f"- Improve micro-interactions and hover states\n"
            f"- Ensure all placeholder data feels realistic and coherent\n"
            f"- Improve any areas that feel rushed or generic\n"
            f"- Make sure the layout is tight and nothing feels accidental\n"
            f"- Ensure animations (if any) are smooth and purposeful\n"
            f"- The result should be something a senior designer would be proud to hand off\n\n"
            f"CURRENT PROTOTYPE HTML:\n{html_content}\n\n"
            f"OUTPUT: A single complete HTML file only. Starts with <!DOCTYPE html>. "
            f"No explanation, no markdown fences, just the HTML."
        ),
        "type": "design",
        "id": polish_id,
        "status": "pending",
        "created_at": now.isoformat(),
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
        "output_file": f"WesaTeam/agent-ecosystem/static/designs/polished-{stem}.html"
    }

    # Create FE1 analysis task (runs AFTER polish due to sequential daemon + later timestamp)
    fe1_id = f"fe1-plan-{uuid.uuid4().hex[:6]}"
    fe1_task = {
        "prompt": (
            f"You are FE1, a frontend expert. Greg has approved the '{concept_name}' dashboard design concept "
            f"and it has been polished by UX1. Your task is to produce an implementation plan - not code yet.\n\n"
            f"APPROVED DESIGN CONCEPT - Key Visual Principles:\n"
            f"File: static/designs/polished-{stem}.html (polished prototype)\n\n"
            f"Produce a structured implementation plan covering:\n"
            f"1. COMPONENT BREAKDOWN: List every UI component needed (names, purpose, props shape)\n"
            f"2. FILE STRUCTURE: Full Next.js App Router file tree for this dashboard\n"
            f"3. STATE MANAGEMENT: What state is needed, where it lives (server vs client components)\n"
            f"4. DATA FLOW: How real task/agent data flows from the FastAPI backend to each component\n"
            f"5. TECH DECISIONS: Any libraries needed beyond React 19 + Next.js + Tailwind + shadcn\n"
            f"6. BUILD ORDER: In what sequence should components be built for fastest iteration\n"
            f"7. RISKS: Any complexity or design elements that will be hard to implement faithfully\n\n"
            f"Output as structured markdown. Be specific and opinionated. This plan goes to Axel for review before any code is written."
        ),
        "type": "frontend",
        "id": fe1_id,
        "status": "pending",
        "created_at": (datetime.fromtimestamp(now.timestamp() + 1)).isoformat(),
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
        "output_file": f"WesaTeam/agent-ecosystem/logs/designs/{fe1_id}_plan.md"
    }

    # Create Q1 review task (runs after polish - sequential daemon, timestamp +2s)
    # Q1 reviews the approved concept on pure design merit.
    # Output: scored review + skill update recommendations for Axel to apply to ux_agent.yaml
    q1_id = f"q1-review-{uuid.uuid4().hex[:6]}"
    q1_task = {
        "prompt": (
            f"Review the following single-file HTML design prototype. "
            f"You have no product context. Evaluate it on pure design merit only.\n\n"
            f"Use your full scoring rubric (Visual Hierarchy, Typography, Color, Spacing & Layout, "
            f"Consistency, Craft & Finish, Originality, Emotional Register). "
            f"Be specific and direct. Reference actual elements from the HTML.\n\n"
            f"Always end with Skill Update Recommendations: concrete rules the designer "
            f"should follow next time, written so a team lead can paste them directly into a skill file.\n\n"
            f"--- DESIGN PROTOTYPE ---\n\n"
            f"{html_content}"
        ),
        "type": "review",
        "id": q1_id,
        "status": "pending",
        "created_at": (datetime.fromtimestamp(now.timestamp() + 2)).isoformat(),
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
        "output_file": f"WesaTeam/agent-ecosystem/logs/designs/{q1_id}_design-review.md"
    }

    # Write task files for daemon to pick up
    (TASKS_DIR / f"{polish_id}.json").write_text(json.dumps(polish_task, indent=2), encoding="utf-8")
    (TASKS_DIR / f"{fe1_id}.json").write_text(json.dumps(fe1_task, indent=2), encoding="utf-8")
    (TASKS_DIR / f"{q1_id}.json").write_text(json.dumps(q1_task, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "concept": concept_name,
        "polish_task": polish_id,
        "fe1_task": fe1_id,
        "q1_review_task": q1_id
    }


@app.post("/api/designs/delete")
async def delete_design(req: ApproveRequest):
    """Delete a concept without retrying — simple dismiss."""
    target = DESIGNS_DIR / req.filename
    if target.exists():
        target.unlink()
    return {"ok": True, "deleted": req.filename}


@app.get("/api/reviews")
async def list_reviews():
    """Return Q1 review markdown files from logs/."""
    reviews = []
    for f in sorted(LOGS_DESIGNS_DIR.glob("q1-*_*.md"), reverse=True):
        reviews.append({
            "filename": f.name,
            "concept": f.stem.replace("q1-review-", "").replace("q1-reject-", "").replace("_design-review", "").replace("_rejection-review", ""),
            "type": "rejection" if "reject" in f.name else "approval",
            "url": f"/api/reviews/{f.name}"
        })
    return reviews


@app.get("/api/reviews/{filename}")
async def get_review(filename: str):
    """Return the content of a Q1 review file."""
    path = LOGS_DESIGNS_DIR / filename
    if not path.exists() or not filename.startswith("q1-"):
        raise HTTPException(status_code=404, detail="Review not found")
    return {"filename": filename, "content": path.read_text(encoding="utf-8")}


@app.post("/api/designs/retry")
async def retry_design(req: RetryRequest):
    """
    Queue a new UX1 design task for a rejected concept with optional feedback.
    The existing file is deleted and a fresh task is created.
    """
    target_path = DESIGNS_DIR / req.filename
    concept_name = Path(req.filename).stem.replace("-", " ").replace("_", " ").title()

    # Read HTML before deleting — Q1 needs it for the rejection review
    rejected_html = ""
    if target_path.exists():
        rejected_html = target_path.read_text(encoding="utf-8")
        target_path.unlink()

    now = datetime.now()

    feedback_section = ""
    if req.feedback.strip():
        feedback_section = f"\n\nSPECIFIC FEEDBACK FROM GREG:\n{req.feedback.strip()}\n\nAddress this feedback directly in your new design."

    retry_id = f"ux1-retry-{uuid.uuid4().hex[:6]}"
    retry_task = {
        "prompt": (
            f"You are UX1, a design specialist. A previous design attempt for '{concept_name}' was rejected. "
            f"You are doing a complete redesign - start fresh, do not repeat what was done before.\n\n"
            f"PRODUCT: WESA Agent Ecosystem Dashboard. Local monitoring tool for a solo technical founder "
            f"watching AI agents process tasks in real-time. Data: task queue (pending/in_progress/done/failed), "
            f"agent assignments (D1=code, W1=write, R1=research, UX1=design, FE1=frontend), task output reports, daemon health.\n\n"
            f"CONCEPT DIRECTION: {concept_name}\n"
            f"This design must be genuinely different from the previous attempt. Push harder. "
            f"Reference the best dashboard UIs of 2025-2026. Every design decision must be intentional and defendable. "
            f"Make it breathtaking. Use realistic placeholder data to make it feel alive.{feedback_section}\n\n"
            f"OUTPUT: A single complete HTML file only. Starts with <!DOCTYPE html>. "
            f"CDN dependencies only (React 18, Babel, Tailwind CDN). No explanation, no markdown fences, just the HTML."
        ),
        "type": "design",
        "id": retry_id,
        "status": "pending",
        "created_at": now.isoformat(),
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
        "output_file": f"WesaTeam/agent-ecosystem/static/designs/{req.filename}"
    }

    # Q1 reviews the rejected design — failure signal is as valuable as success signal
    q1_id = f"q1-reject-{uuid.uuid4().hex[:6]}"
    q1_task = {
        "prompt": (
            f"Review the following single-file HTML design prototype. "
            f"Context: this design was REJECTED by the client. Your review should focus on diagnosing why — "
            f"what specific design failures likely caused the rejection.\n\n"
            f"Evaluate on pure design merit using your full scoring rubric "
            f"(Visual Hierarchy, Typography, Color, Spacing & Layout, Consistency, Craft & Finish, Originality, Emotional Register). "
            f"Be specific. Reference actual elements from the HTML.\n\n"
            f"The Skill Update Recommendations section is critical here: identify the patterns that failed "
            f"so the designer can avoid repeating them. Write rules the designer must follow next time.\n\n"
            f"--- REJECTED DESIGN ---\n\n"
            f"{rejected_html}"
        ),
        "type": "review",
        "id": q1_id,
        "status": "pending",
        "created_at": (datetime.fromtimestamp(now.timestamp() + 1)).isoformat(),
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
        "output_file": f"WesaTeam/agent-ecosystem/logs/designs/{q1_id}_rejection-review.md"
    }

    (TASKS_DIR / f"{retry_id}.json").write_text(json.dumps(retry_task, indent=2), encoding="utf-8")
    (TASKS_DIR / f"{q1_id}.json").write_text(json.dumps(q1_task, indent=2), encoding="utf-8")

    return {"ok": True, "concept": concept_name, "retry_task": retry_id, "q1_review_task": q1_id}


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