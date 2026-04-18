"""
Lumio Time Watcher
Tracks study sessions per user.
Behaviour (write/read): records start/end of sessions, calculates daily totals.
Future: enforce limits, send notifications to Greg.
Data stored as JSON per user in /data/sessions/{username}.json
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger("time_watcher")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

SESSION_DATA_DIR = Path(os.environ.get("SESSION_DATA_DIR", "/data/sessions"))
SESSION_DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Lumio Time Watcher", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------
def user_data_path(username: str) -> Path:
    return SESSION_DATA_DIR / f"{username}.json"


def load_user_data(username: str) -> dict:
    path = user_data_path(username)
    if path.exists():
        return json.loads(path.read_text())
    return {"username": username, "sessions": [], "active_session": None}


def save_user_data(username: str, data: dict):
    path = user_data_path(username)
    path.write_text(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class SessionStartRequest(BaseModel):
    username: str


class SessionEndRequest(BaseModel):
    username: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "data_dir": str(SESSION_DATA_DIR)}


@app.post("/session/start")
def start_session(req: SessionStartRequest):
    """Record that a user started a study session."""
    data = load_user_data(req.username)
    if data["active_session"]:
        return {"status": "already_active", "started_at": data["active_session"]["started_at"]}
    data["active_session"] = {"started_at": datetime.utcnow().isoformat()}
    save_user_data(req.username, data)
    return {"status": "started", "username": req.username, "started_at": data["active_session"]["started_at"]}


@app.post("/session/end")
def end_session(req: SessionEndRequest):
    """Record end of session, calculate duration, archive it."""
    data = load_user_data(req.username)
    if not data["active_session"]:
        return {"status": "no_active_session"}

    started_at = datetime.fromisoformat(data["active_session"]["started_at"])
    ended_at = datetime.utcnow()
    duration_seconds = int((ended_at - started_at).total_seconds())

    session_record = {
        "date": ended_at.date().isoformat(),
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": duration_seconds,
        "duration_minutes": round(duration_seconds / 60, 1),
    }

    data["sessions"].append(session_record)
    data["active_session"] = None
    save_user_data(req.username, data)

    log.info(f"[{req.username}] Session ended: {duration_seconds}s")
    return {"status": "ended", "username": req.username, **session_record}


@app.get("/stats/{username}")
def get_stats(username: str, days: int = 7):
    """Return study time stats for last N days."""
    data = load_user_data(username)
    sessions = data.get("sessions", [])

    today = date.today()
    daily_totals = {}

    for s in sessions:
        session_date = s.get("date", "")
        if not session_date:
            continue
        try:
            d = date.fromisoformat(session_date)
        except ValueError:
            continue
        delta = (today - d).days
        if delta <= days:
            daily_totals[session_date] = daily_totals.get(session_date, 0) + s.get("duration_minutes", 0)

    return {
        "username": username,
        "active_session": data.get("active_session"),
        "total_sessions": len(sessions),
        "daily_totals_minutes": daily_totals,
        "period_days": days,
    }


@app.get("/stats")
def get_all_stats():
    """Summary stats for all users — for Greg's overview."""
    results = []
    for path in SESSION_DATA_DIR.glob("*.json"):
        username = path.stem
        data = json.loads(path.read_text())
        sessions = data.get("sessions", [])
        today = date.today().isoformat()
        today_minutes = sum(
            s.get("duration_minutes", 0)
            for s in sessions
            if s.get("date") == today
        )
        results.append({
            "username": username,
            "active": bool(data.get("active_session")),
            "total_sessions": len(sessions),
            "today_minutes": round(today_minutes, 1),
        })
    return {"users": results}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8770)))
