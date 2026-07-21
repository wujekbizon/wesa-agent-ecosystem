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

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --- Path & Environment Setup ---
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from src.task_queue import TaskQueue
from src.staging_queue import StagingQueue
from secure_core.control_plane import verify_dashboard_token
from src.config import get_config
from src.logging_setup import configure_logging

_cfg = get_config()

# E0.6: structured JSON logging (logs/structured.jsonl), additive alongside
# existing dashboard behavior. See daemon.py for the same setup + rationale.
configure_logging()

# --- Configuration ---
PORT = 8765
LUMIO_INFRA_MANAGER_URL = _cfg.lumio_infra_manager_url
DB_PATH = BASE_DIR / "logs" / "tasks.db"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DESIGNS_DIR = LOGS_DIR / "designs"
LOGS_RESEARCH_DIR = LOGS_DIR / "research"
LOGS_DEV_DIR = LOGS_DIR / "dev"
TASKS_DIR = BASE_DIR / "tasks"
STATIC_DIR = BASE_DIR / "static"
DESIGNS_DIR = STATIC_DIR / "designs"
WESA_DIR = BASE_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"
WOLFMED_DATA_DIR = EXTRACTOR_DIR / "data"
WOLFMED_OUTPUTS_DIR = EXTRACTOR_DIR / "outputs"
WOLFMED_DONE_DIR = EXTRACTOR_DIR / "done"
WOLFMED_INTAKE_DIR = EXTRACTOR_DIR / "intake"
DASHBOARD_FILE = STATIC_DIR / "dashboard.html"
PID_FILE = LOGS_DIR / "daemon.pid"
DAEMON_SCRIPT = BASE_DIR / "daemon.py"

# --- Initialization ---
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DESIGNS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DEV_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
DESIGNS_DIR.mkdir(parents=True, exist_ok=True)

queue = TaskQueue(db_path=str(DB_PATH))
staging = StagingQueue(db_path=str(LOGS_DIR / "staging.db"))

app = FastAPI(
    title="WESA Web Monitor",
    description="Local dashboard for WESA Agent Ecosystem",
    version="1.1.0"
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/designs", StaticFiles(directory=str(DESIGNS_DIR)), name="designs")

# --- S0 control-plane lockdown ---
# All mutating requests (POST/DELETE) require a bearer token matching the
# vault-stored DASHBOARD_TOKEN (secure_core.control_plane). GETs remain
# open for local dashboard viewing. Run `python -m secure_core.control_plane`
# once to provision the token, then add it as an Authorization: Bearer
# header in the dashboard UI / browser bookmark.
@app.middleware("http")
async def require_dashboard_token(request: Request, call_next):
    if request.method in ("POST", "DELETE"):
        auth = request.headers.get("authorization")
        if not verify_dashboard_token(auth):
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)

# --- Dashboard ---

@app.get("/")
async def root():
    if not DASHBOARD_FILE.exists():
        return FileResponse(STATIC_DIR / "404.html", status_code=404)
    return FileResponse(str(DASHBOARD_FILE))

# --- Tasks ---

@app.get("/api/tasks")
async def get_tasks():
    return queue.get_all()

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task_data = queue.get_all()
    task: Optional[Dict[str, Any]] = next((t for t in task_data if t.get("id") == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
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
    json_file = TASKS_DIR / f"{task_id}.json"
    if json_file.exists():
        try:
            json_file.unlink()
        except PermissionError:
            pass
    return {"ok": True}

# --- Designs ---

@app.get("/api/designs")
async def list_designs():
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
    approved_path = DESIGNS_DIR / req.filename
    if not approved_path.exists():
        raise HTTPException(status_code=404, detail=f"Design file '{req.filename}' not found")

    html_content = approved_path.read_text(encoding="utf-8")
    stem = Path(req.filename).stem
    concept_name = stem.replace("-", " ").replace("_", " ").title()

    approved_filename = f"approved-{stem}.html"
    approved_final = DESIGNS_DIR / approved_filename
    approved_final.write_text(html_content, encoding="utf-8")
    try:
        approved_path.unlink()
    except Exception:
        pass

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
            f"Output as structured markdown. Be specific and opinionated."
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
            f"--- DESIGN PROTOTYPE ---\n\n{html_content}"
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

    (TASKS_DIR / f"{polish_id}.json").write_text(json.dumps(polish_task, indent=2), encoding="utf-8")
    (TASKS_DIR / f"{fe1_id}.json").write_text(json.dumps(fe1_task, indent=2), encoding="utf-8")
    (TASKS_DIR / f"{q1_id}.json").write_text(json.dumps(q1_task, indent=2), encoding="utf-8")

    return {"ok": True, "concept": concept_name, "polish_task": polish_id, "fe1_task": fe1_id, "q1_review_task": q1_id}


@app.post("/api/designs/delete")
async def delete_design(req: ApproveRequest):
    target = DESIGNS_DIR / req.filename
    if target.exists():
        target.unlink()
    return {"ok": True, "deleted": req.filename}


@app.get("/api/reviews")
async def list_reviews():
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
    path = LOGS_DESIGNS_DIR / filename
    if not path.exists() or not filename.startswith("q1-"):
        raise HTTPException(status_code=404, detail="Review not found")
    return {"filename": filename, "content": path.read_text(encoding="utf-8")}


@app.post("/api/designs/retry")
async def retry_design(req: RetryRequest):
    target_path = DESIGNS_DIR / req.filename
    concept_name = Path(req.filename).stem.replace("-", " ").replace("_", " ").title()

    rejected_html = ""
    if target_path.exists():
        rejected_html = target_path.read_text(encoding="utf-8")
        target_path.unlink()

    now = datetime.now()
    feedback_section = f"\n\nSPECIFIC FEEDBACK FROM GREG:\n{req.feedback.strip()}\n\nAddress this feedback directly in your new design." if req.feedback.strip() else ""

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

    q1_id = f"q1-reject-{uuid.uuid4().hex[:6]}"
    q1_task = {
        "prompt": (
            f"Review the following single-file HTML design prototype. "
            f"Context: this design was REJECTED by the client. Your review should focus on diagnosing why — "
            f"what specific design failures likely caused the rejection.\n\n"
            f"Evaluate on pure design merit using your full scoring rubric. "
            f"Be specific. Reference actual elements from the HTML.\n\n"
            f"The Skill Update Recommendations section is critical here: identify the patterns that failed "
            f"so the designer can avoid repeating them.\n\n"
            f"--- REJECTED DESIGN ---\n\n{rejected_html}"
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

# --- Staging Queue ---

@app.get("/api/staging")
async def get_staging():
    """Return briefs awaiting Axel review."""
    items = staging.get_awaiting()
    return [
        {
            "id": item["id"],
            "topic": item["topic"],
            "source_pipeline": item["source_pipeline"],
            "brief_content": item["brief_content"],
            "created_at": item["created_at"],
        }
        for item in items
    ]

@app.post("/api/staging/{brief_id}/approve")
async def approve_brief(brief_id: str):
    staging.update_status(brief_id, "approved")
    return {"ok": True}

@app.post("/api/staging/{brief_id}/discard")
async def discard_brief(brief_id: str):
    staging.update_status(brief_id, "discarded")
    return {"ok": True}

# --- Research Center ---

RC_BRIEFS_FILE    = LOGS_RESEARCH_DIR / "briefs.json"
RC_SCHEDULE_FILE2 = LOGS_DIR / "rc_schedule.json"   # same as RC_SCHEDULE_FILE above — alias for clarity
RC_TOPICS_FILE    = BASE_DIR / "agents" / "pipelines" / "rc_topics.json"


def _load_briefs() -> List[Dict[str, Any]]:
    """Load the briefs store from disk. Returns [] if missing/corrupt."""
    if not RC_BRIEFS_FILE.exists():
        return []
    try:
        return json.loads(RC_BRIEFS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_briefs(briefs: List[Dict[str, Any]]) -> None:
    RC_BRIEFS_FILE.write_text(json.dumps(briefs, indent=2, ensure_ascii=False), encoding="utf-8")


@app.get("/api/research/briefs")
async def list_research_briefs():
    """Return summary list of all research briefs (no heavy content fields)."""
    briefs = _load_briefs()
    return [
        {
            "id":         b.get("id"),
            "topic":      b.get("topic"),
            "rt1_score":  b.get("rt1_score"),
            "decision":   b.get("decision", "pending"),
            "created_at": b.get("created_at"),
        }
        for b in sorted(briefs, key=lambda x: x.get("created_at", ""), reverse=True)
    ]


@app.get("/api/research/briefs/{brief_id}")
async def get_research_brief(brief_id: str):
    """Return full brief including R1 research, W1 synthesis, RT1 score + rationale."""
    briefs = _load_briefs()
    brief = next((b for b in briefs if b.get("id") == brief_id), None)
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    # Attempt to load content from companion markdown files if not embedded
    def _try_load(field: str, glob_pattern: str) -> str:
        if brief.get(field):
            return brief[field]
        for f in LOGS_RESEARCH_DIR.glob(glob_pattern):
            try:
                return f.read_text(encoding="utf-8")
            except Exception:
                pass
        return ""

    brief_id_short = brief_id[:8] if len(brief_id) >= 8 else brief_id
    brief.setdefault("research",       _try_load("research",       f"*{brief_id_short}*_r1.md"))
    brief.setdefault("synthesis",      _try_load("synthesis",      f"*{brief_id_short}*_w1.md"))
    brief.setdefault("rt1_rationale",  _try_load("rt1_rationale",  f"*{brief_id_short}*_rt1.md"))

    return brief


@app.get("/api/research/staging")
async def get_research_staging():
    """Return items currently in the research staging queue."""
    try:
        items = staging.get_awaiting()
        return [
            {
                "id":              item.get("id"),
                "topic":           item.get("topic", ""),
                "source_pipeline": item.get("source_pipeline", ""),
                "created_at":      item.get("created_at", ""),
            }
            for item in items
        ]
    except Exception:
        return []


@app.get("/api/research/meta")
async def get_research_meta():
    """Return scheduler metadata: last SC1/SY1 run times and topic rotation state."""
    schedule: Dict[str, Any] = {}
    if RC_SCHEDULE_FILE.exists():
        try:
            schedule = json.loads(RC_SCHEDULE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Topic rotation list
    topics: List[Dict[str, Any]] = []
    if RC_TOPICS_FILE.exists():
        try:
            raw = json.loads(RC_TOPICS_FILE.read_text(encoding="utf-8"))
            next_idx = schedule.get("next_topic_index", 0)
            for i, t in enumerate(raw):
                label = t if isinstance(t, str) else t.get("label", str(t))
                topics.append({"label": label, "active": i == next_idx})
        except Exception:
            pass

    return {
        "last_sc1":          schedule.get("last_sc1_at"),
        "last_sy1":          schedule.get("last_sy1_at"),
        "last_triggered_at": schedule.get("last_triggered_at"),
        "next_topic_index":  schedule.get("next_topic_index", 0),
        "topics":            topics,
    }


class RCTriggerRequest(BaseModel):
    topic: str = ""


@app.post("/api/research/trigger")
async def trigger_research_center(req: RCTriggerRequest):
    """Queue a research-center pipeline run. Uses next scheduled topic if none given."""
    # Resolve topic from rotation if not supplied
    topic = req.topic.strip()
    if not topic:
        schedule: Dict[str, Any] = {}
        if RC_SCHEDULE_FILE.exists():
            try:
                schedule = json.loads(RC_SCHEDULE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        if RC_TOPICS_FILE.exists():
            try:
                raw = json.loads(RC_TOPICS_FILE.read_text(encoding="utf-8"))
                idx = schedule.get("next_topic_index", 0) % max(len(raw), 1)
                entry = raw[idx]
                topic = entry if isinstance(entry, str) else entry.get("label", "")
            except Exception:
                pass
        if not topic:
            topic = "WesaTeam ecosystem updates"

    task_id = f"rc-trigger-{uuid.uuid4().hex[:6]}"
    task = {
        "id":           task_id,
        "type":         "coordinate",
        "prompt":       f"START_PIPELINE:research-center TOPIC:{topic}",
        "status":       "pending",
        "created_at":   datetime.now().isoformat(),
        "completed_at": "",
        "result":       "",
        "error":        "",
        "model_used":   "",
        "output_file":  "",
    }
    (TASKS_DIR / f"{task_id}.json").write_text(json.dumps(task, indent=2), encoding="utf-8")
    return {"ok": True, "task_id": task_id, "topic": topic}


class RCDecisionRequest(BaseModel):
    brief_id: str
    decision: str   # "approve" | "hold" | "discard"


@app.post("/api/research/decision")
async def set_research_decision(req: RCDecisionRequest):
    """Set the Axel decision on a research brief (approve / hold / discard)."""
    allowed = {"approve", "hold", "discard", "pending"}
    decision = req.decision.strip().lower()
    if decision not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid decision '{decision}'. Must be one of: {', '.join(sorted(allowed))}")

    briefs = _load_briefs()
    brief = next((b for b in briefs if b.get("id") == req.brief_id), None)
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    brief["decision"]    = decision
    brief["decided_at"]  = datetime.now().isoformat()
    _save_briefs(briefs)

    # Also sync to staging queue if the brief lives there
    try:
        if decision == "approve":
            staging.update_status(req.brief_id, "approved")
        elif decision == "discard":
            staging.update_status(req.brief_id, "discarded")
    except Exception:
        pass

    return {"ok": True, "brief_id": req.brief_id, "decision": decision}



class TaskSubmitRequest(BaseModel):
    type: str
    prompt: str
    output_file: str = ""

VALID_TASK_TYPES = {"research", "write", "code", "design", "frontend", "review", "test", "assets", "general", "git", "extract_pdf", "validate_questions"}

@app.post("/api/tasks")
async def submit_task(req: TaskSubmitRequest):
    task_type = req.type.strip().lower()
    if task_type not in VALID_TASK_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid task type '{task_type}'. Must be one of: {', '.join(sorted(VALID_TASK_TYPES))}")
    # git tasks allow empty prompt — daemon auto-injects live git diff context
    if not req.prompt.strip() and task_type != "git":
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    task_id = f"manual-{uuid.uuid4().hex[:8]}"
    task = {
        "id": task_id,
        "type": task_type,
        "prompt": req.prompt.strip(),
        "output_file": req.output_file.strip(),
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
    }
    (TASKS_DIR / f"{task_id}.json").write_text(json.dumps(task, indent=2), encoding="utf-8")
    queue.import_task(task)
    return {"ok": True, "task_id": task_id}

# --- Pipeline Trigger ---

class PipelineStartRequest(BaseModel):
    pipeline: str   # e.g. "research-center"
    topic: str = "" # e.g. "AgentHire portal design patterns"

@app.post("/api/pipeline/start")
async def start_pipeline(req: PipelineStartRequest):
    """
    Drop a coordinate task that triggers daemon to start a named pipeline.
    The daemon picks it up within POLL_INTERVAL seconds.
    """
    task_id = f"pipeline-trigger-{uuid.uuid4().hex[:6]}"
    task = {
        "id": task_id,
        "type": "coordinate",
        "prompt": f"START_PIPELINE:{req.pipeline} TOPIC:{req.topic}",
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
        "output_file": "",
    }
    (TASKS_DIR / f"{task_id}.json").write_text(json.dumps(task, indent=2), encoding="utf-8")
    queue.import_task(task)
    return {"ok": True, "task_id": task_id, "pipeline": req.pipeline, "topic": req.topic}

# --- Wolfmed ---

def _load_wolfmed_manifests():
    if not WOLFMED_DATA_DIR.exists(): return []
    result = []
    for f in sorted(WOLFMED_DATA_DIR.glob("*_manifest.json")):
        try: result.append(json.loads(f.read_text(encoding="utf-8")))
        except: continue
    return result

@app.get("/api/wolfmed/books")
async def list_wolfmed_books():
    manifests = _load_wolfmed_manifests()
    out = []
    for m in manifests:
        counts = {"pending":0,"queued":0,"extracted":0,"validating":0,"validated":0}
        for c in m.get("chunks",[]):
            s = c.get("status","pending")
            counts[s] = counts.get(s,0) + 1
        out.append({
            "book": m.get("book"),
            "category": m.get("category"),
            "mode": m.get("mode","generate"),
            "total_chunks": m.get("total_chunks",0),
            "validated_count": m.get("validated_count",0),
            "merge_status": m.get("merge_status","pending"),
            "created_at": m.get("created_at"),
            "updated_at": m.get("updated_at"),
            "chunk_statuses": counts,
            # Trim fields (present only if chunker trimming was used)
            "total_pages": m.get("total_pages"),
            "content_pages": m.get("content_pages"),
            "content_start_page": m.get("content_start_page"),
            "content_end_page": m.get("content_end_page"),
            "skipped_front": m.get("skipped_front", 0),
            "skipped_back": m.get("skipped_back", 0),
            "auto_trim": m.get("auto_trim") is not None,
        })
    return out

@app.get("/api/wolfmed/books/{book}")
async def get_wolfmed_book(book: str):
    p = WOLFMED_DATA_DIR / f"{book}_manifest.json"
    if not p.exists(): raise HTTPException(status_code=404, detail=f"Manifest not found: {book}")
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/wolfmed/stats")
async def get_wolfmed_stats():
    manifests = _load_wolfmed_manifests()
    books   = [m for m in manifests if m.get("mode","generate") == "generate"]
    slides  = [m for m in manifests if m.get("mode","generate") == "generate_slides"]
    return {
        "total_books":       len(books),
        "merged_books":      sum(1 for m in books  if m.get("merge_status") == "done"),
        "total_chunks":      sum(m.get("total_chunks",0) for m in books),
        "validated_chunks":  sum(m.get("validated_count",0) for m in books),
        "total_slides":      len(slides),
        "merged_slides":     sum(1 for m in slides if m.get("merge_status") == "done"),
        "in_progress_books": sum(1 for m in manifests if m.get("merge_status") not in ("done","failed")),
    }

# --- Daemon Control ---

@app.get("/api/daemon/status")
async def daemon_status():
    pid = None
    if PID_FILE.exists():
        try:
            content = PID_FILE.read_text(encoding="utf-8").strip()
            pid = int(content)
            os.kill(pid, 0)
            return {"running": True, "pid": pid}
        except (ValueError, ProcessLookupError, OSError):
            return {"running": False, "pid": pid}
    return {"running": False, "pid": None}

RC_ENABLED_FILE = LOGS_DIR / "rc_enabled.flag"
RC_SCHEDULE_FILE = LOGS_DIR / "rc_schedule.json"

@app.get("/api/scheduler/status")
async def scheduler_status():
    enabled = RC_ENABLED_FILE.exists()
    schedule = {}
    if RC_SCHEDULE_FILE.exists():
        try:
            schedule = json.loads(RC_SCHEDULE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "enabled": enabled,
        "last_triggered_at": schedule.get("last_triggered_at"),
        "next_topic_index": schedule.get("next_topic_index", 0),
    }

@app.post("/api/scheduler/enable")
async def scheduler_enable():
    RC_ENABLED_FILE.write_text("enabled", encoding="utf-8")
    return {"ok": True, "enabled": True}

@app.post("/api/scheduler/disable")
async def scheduler_disable():
    RC_ENABLED_FILE.unlink(missing_ok=True)
    return {"ok": True, "enabled": False}

PIPELINES_RUNNING_DIR = BASE_DIR / "agents" / "pipelines" / "running"

@app.get("/api/pipelines")
@app.get("/api/pipelines/running")
async def get_running_pipelines():
    runs = []
    if not PIPELINES_RUNNING_DIR.exists():
        return runs
    for f in sorted(PIPELINES_RUNNING_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            runs.append({
                "run_id": data.get("run_id", f.stem),
                "pipeline": data.get("pipeline", ""),
                "topic": data.get("topic", ""),
                "status": data.get("status", ""),
                "iteration": data.get("iteration", 1),
                "started_at": data.get("started_at", ""),
                "ended_at": data.get("ended_at", ""),
                "stop_reason": data.get("stop_reason", ""),
            })
        except Exception:
            continue
    return runs

@app.post("/api/daemon/restart")
async def daemon_restart():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, OSError):
            pass
    await asyncio.sleep(1.5)
    proc = subprocess.Popen(
        [sys.executable, "daemon.py"],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    try:
        PID_FILE.write_text(str(proc.pid))
    except PermissionError:
        pass
    return {"ok": True, "pid": proc.pid}

# --- Axel Log ---

AXEL_TRIGGER_LOG = LOGS_DIR / "axel_trigger.log"
AXEL_LOG_FILE    = LOGS_DIR / "AXEL_LOG.md"

@app.get("/api/logs/axel")
async def get_axel_log(lines: int = 120):
    """Return the last N lines of axel_trigger.log (headless session output)."""
    if not AXEL_TRIGGER_LOG.exists():
        return {"lines": [], "exists": False}
    try:
        content = AXEL_TRIGGER_LOG.read_text(encoding="utf-8", errors="replace")
        all_lines = content.splitlines()
        return {"lines": all_lines[-lines:], "exists": True, "total": len(all_lines)}
    except Exception as e:
        return {"lines": [f"Error reading log: {e}"], "exists": True, "total": 0}

@app.get("/api/logs/axel-activity")
async def get_axel_activity_log(lines: int = 80):
    """Return the last N lines of AXEL_LOG.md (append-only activity log)."""
    if not AXEL_LOG_FILE.exists():
        return {"lines": [], "exists": False}
    try:
        content = AXEL_LOG_FILE.read_text(encoding="utf-8", errors="replace")
        all_lines = content.splitlines()
        return {"lines": all_lines[-lines:], "exists": True, "total": len(all_lines)}
    except Exception as e:
        return {"lines": [f"Error reading log: {e}"], "exists": True, "total": 0}

# --- Lumio Infra Proxy ---

async def _infra_get(path: str) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{LUMIO_INFRA_MANAGER_URL}{path}")
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Infra manager unreachable. Is Debian VM running?")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _infra_post(path: str, body: dict = None) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{LUMIO_INFRA_MANAGER_URL}{path}", json=body or {})
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Infra manager unreachable. Is Debian VM running?")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/infra/health")
async def infra_health():
    return await _infra_get("/health")


@app.get("/api/infra/services")
async def infra_list_services():
    return await _infra_get("/services")


@app.get("/api/infra/services/{service_name}")
async def infra_get_service(service_name: str):
    return await _infra_get(f"/services/{service_name}")


@app.post("/api/infra/services/{service_name}/start")
async def infra_start_service(service_name: str):
    return await _infra_post(f"/services/{service_name}/start")


@app.post("/api/infra/services/{service_name}/stop")
async def infra_stop_service(service_name: str):
    return await _infra_post(f"/services/{service_name}/stop")


@app.post("/api/infra/services/{service_name}/restart")
async def infra_restart_service(service_name: str):
    return await _infra_post(f"/services/{service_name}/restart")


@app.post("/api/infra/services/start-all")
async def infra_start_all():
    return await _infra_post("/services/start-all")


@app.post("/api/infra/services/stop-all")
async def infra_stop_all():
    return await _infra_post("/services/stop-all")


@app.get("/api/infra/services/{service_name}/logs")
async def infra_get_logs(service_name: str, tail: int = 100):
    return await _infra_get(f"/services/{service_name}/logs?tail={tail}")


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json(queue.get_all())
    try:
        while True:
            await websocket.send_json(queue.get_all())
            await asyncio.sleep(2)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass

# --- Entry Point ---

if __name__ == "__main__":
    import uvicorn
    # S0: bind to localhost only — dashboard was previously exposed on all
    # interfaces with zero auth. Remote access (if ever needed) goes through
    # an authenticated tunnel, not a raw 0.0.0.0 bind.
    uvicorn.run("web_monitor:app", host="127.0.0.1", port=PORT, reload=False, log_level="info")