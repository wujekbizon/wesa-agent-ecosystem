"""
RC Queue Manager — src/rc_queue_manager.py

Manages three things:
  1. logs/rc_queue.json           — dynamic topic queue (SC1/SY1 write, research-center reads)
  2. logs/knowledge_index.json    — append-only brief index (RT1 saves here after AUTO-SAVE)
  3. RT1 routing                  — parse RT1 output and dispatch to staging or knowledge store

Used by:
  - daemon.py               → auto_run_research_center() reads next topic from queue
  - pipeline_controller.py  → stage_action: rt1_route calls handle_rt1_result()
  - SC1/SY1 completion hook → merge_scout_results() called after scout pipelines complete
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# All logs/* paths — consistent with existing daemon.py conventions
QUEUE_FILE                 = Path("logs/rc_queue.json")           # alongside rc_schedule.json
KNOWLEDGE_INDEX_FILE       = Path("logs/knowledge_index.json")
KNOWLEDGE_RECENT_HIGH_FILE = Path("logs/knowledge_recent_high.json")
KNOWLEDGE_BRIEFS_DIR       = Path("logs/research/briefs")         # full brief files in research/

MAX_PENDING        = 50
DEDUP_WINDOW_DAYS  = 30


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

def load_queue() -> dict:
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "version": "1.0",
        "last_scout_run": None,
        "last_synthesis_run": None,
        "pending": [],
        "in_progress": [],
        "completed_today": 0,
        "discarded_today": 0,
    }


def save_queue(queue: dict) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")


def next_topic(queue: dict) -> Optional[dict]:
    """
    Pop the highest-priority pending topic (high priority first, then descending composite score).
    Moves the item to in_progress atomically. Returns topic dict or None if queue is empty.
    """
    pending = queue.get("pending", [])
    if not pending:
        return None

    def sort_key(t):
        return (0 if t.get("priority") == "high" else 1, -t.get("composite_score", 0))

    pending.sort(key=sort_key)
    topic = pending.pop(0)
    topic["claimed_at"] = datetime.now().isoformat()
    queue.setdefault("in_progress", []).append(topic)
    return topic


def complete_topic(queue: dict, topic_id: str) -> None:
    """Move a topic out of in_progress and increment the completed counter."""
    queue["in_progress"] = [t for t in queue.get("in_progress", []) if t.get("id") != topic_id]
    queue["completed_today"] = queue.get("completed_today", 0) + 1


def merge_scout_results(raw_output: str, channel: str, queue: dict) -> int:
    """
    Parse SC1 or SY1 JSON output and merge valid candidates into the queue.
    Handles deduplication, score filtering, and max depth enforcement.
    Returns count of candidates added.
    """
    candidates = _parse_json_array(raw_output)
    if not candidates:
        return 0

    existing_titles = {t.get("title", "").lower() for t in queue.get("pending", [])}
    existing_titles |= {t.get("title", "").lower() for t in queue.get("in_progress", [])}
    knowledge_titles = _get_recent_knowledge_titles()

    added = 0
    for c in candidates:
        title = c.get("title", "").strip()
        composite = float(c.get("composite", 0))

        if not title:
            continue
        if composite < 2.5:
            queue["discarded_today"] = queue.get("discarded_today", 0) + 1
            continue
        if title.lower() in existing_titles:
            continue
        if _is_recent_duplicate(title, knowledge_titles):
            queue["discarded_today"] = queue.get("discarded_today", 0) + 1
            continue

        topic_id = f"topic_{datetime.now().strftime('%Y%m%d')}_{abs(hash(title)) % 100000:06x}"
        entry = {
            "id": topic_id,
            "title": title,
            "domain": c.get("domain", "unknown"),
            "channel": channel,
            "source_signal": c.get("source_signal", ""),
            "composite_score": composite,
            "priority": c.get("priority", "normal"),
            "created_at": c.get("scouted_at") or datetime.now().isoformat(),
            "status": "pending",
        }
        if channel == "synthesis":
            entry["synthesis_channel"] = c.get("synthesis_channel", "")

        queue.setdefault("pending", []).append(entry)
        existing_titles.add(title.lower())
        added += 1

    # Enforce max queue depth — drop lowest-scoring normal-priority items first
    pending = queue.get("pending", [])
    if len(pending) > MAX_PENDING:
        normal = sorted(
            [t for t in pending if t.get("priority") != "high"],
            key=lambda t: t.get("composite_score", 0)
        )
        to_drop = len(pending) - MAX_PENDING
        drop_ids = {t["id"] for t in normal[:to_drop]}
        queue["pending"] = [t for t in pending if t["id"] not in drop_ids]
        queue["discarded_today"] = queue.get("discarded_today", 0) + to_drop

    return added


# ---------------------------------------------------------------------------
# RT1 routing
# ---------------------------------------------------------------------------

def handle_rt1_result(
    rt1_output: str,
    topic: str,
    brief_content: str,
    raw_research: str,
    staging_queue,
) -> str:
    """
    Parse RT1's JSON decision and dispatch accordingly.
    Returns: "ESCALATE", "AUTO-SAVE", or "DISCARD".
    """
    decision_data = _parse_json_object(rt1_output)
    if not decision_data:
        # RT1 failed to return valid JSON — default to AUTO-SAVE to avoid losing the brief
        decision_data = {
            "decision": "AUTO-SAVE",
            "composite_score": 0.0,
            "reason": "RT1 output parse failed — defaulting to AUTO-SAVE",
            "scores": {},
            "veto_checks": {"domain_aligned": True, "duplicate_found": False, "duplicate_match": None},
        }

    decision = decision_data.get("decision", "AUTO-SAVE").upper()
    score = float(decision_data.get("composite_score", 0))

    if decision == "DISCARD":
        _log_discard(topic, decision_data.get("reason", ""))
        return "DISCARD"

    # Save to knowledge store for both ESCALATE and AUTO-SAVE
    brief_id = _save_to_knowledge_store(topic, brief_content, decision_data)

    if decision == "ESCALATE" and staging_queue:
        staging_queue.add_brief(
            topic=topic,
            brief_content=brief_content,
            raw_research=raw_research,
            source_pipeline="research-center",
        )
        print(f"[RT1] ESCALATED: {topic[:60]} (score={score:.1f}, id={brief_id})")
    else:
        print(f"[RT1] AUTO-SAVED: {topic[:60]} (score={score:.1f}, id={brief_id})")

    return decision


# ---------------------------------------------------------------------------
# Knowledge store
# ---------------------------------------------------------------------------

def _save_to_knowledge_store(topic: str, brief_content: str, decision_data: dict) -> str:
    """Write full brief to logs/research/briefs/ and update logs/knowledge_index.json."""
    KNOWLEDGE_BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

    brief_id = f"brief_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{abs(hash(topic)) % 10000:05x}"
    score = float(decision_data.get("composite_score", 0))

    brief_file = KNOWLEDGE_BRIEFS_DIR / f"{brief_id}.md"
    brief_file.write_text(
        f"# {topic}\n\n"
        f"**Brief ID**: {brief_id}  \n"
        f"**Saved at**: {datetime.now().isoformat()}  \n"
        f"**RT1 score**: {score}  \n"
        f"**Decision**: {decision_data.get('decision', '?')}  \n"
        f"**Reason**: {decision_data.get('reason', '')}  \n\n"
        f"---\n\n"
        f"{brief_content}",
        encoding="utf-8",
    )

    index = _load_knowledge_index()
    index["briefs"].insert(0, {
        "id": brief_id,
        "title": topic,
        "summary": _extract_summary(brief_content),
        "domain": _infer_domain(topic),
        "composite_score": score,
        "saved_at": datetime.now().isoformat(),
        "decision": decision_data.get("decision", "AUTO-SAVE"),
    })
    index["total_briefs"] = len(index["briefs"])
    index["last_updated"] = datetime.now().isoformat()
    _save_knowledge_index(index)
    _rebuild_recent_high(index)

    return brief_id


def _load_knowledge_index() -> dict:
    if KNOWLEDGE_INDEX_FILE.exists():
        try:
            return json.loads(KNOWLEDGE_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": "1.0", "last_updated": None, "total_briefs": 0, "briefs": []}


def _save_knowledge_index(index: dict) -> None:
    KNOWLEDGE_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _rebuild_recent_high(index: dict) -> None:
    """Rebuild logs/knowledge_recent_high.json — briefs from last 7 days with score >= 15."""
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    recent_high = [
        b for b in index.get("briefs", [])
        if b.get("composite_score", 0) >= 15 and (b.get("saved_at") or "") >= cutoff
    ]
    KNOWLEDGE_RECENT_HIGH_FILE.write_text(
        json.dumps(
            {"generated_at": datetime.now().isoformat(), "briefs": recent_high},
            indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )


def _get_recent_knowledge_titles() -> set:
    index = _load_knowledge_index()
    cutoff = (datetime.now() - timedelta(days=DEDUP_WINDOW_DAYS)).isoformat()
    return {
        b["title"].lower()
        for b in index.get("briefs", [])
        if (b.get("saved_at") or "") >= cutoff
    }


def _is_recent_duplicate(title: str, known_titles: set) -> bool:
    title_lower = title.lower()
    if title_lower in known_titles:
        return True
    title_words = set(title_lower.split())
    for known in known_titles:
        known_words = set(known.split())
        if len(title_words) > 2 and len(title_words & known_words) / len(title_words) >= 0.6:
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_array(text: str) -> list:
    text = re.sub(r"```(?:json)?\n?", "", text).strip().rstrip("`")
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return []


def _parse_json_object(text: str) -> Optional[dict]:
    text = re.sub(r"```(?:json)?\n?", "", text).strip().rstrip("`")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _extract_summary(brief_content: str) -> str:
    match = re.search(r"## Context\s*\n(.+?)(?:\n\n|\n##)", brief_content, re.DOTALL)
    if match:
        return match.group(1).strip().split(". ")[0][:200]
    return brief_content.strip()[:120]


def _infer_domain(topic: str) -> str:
    topic_lower = topic.lower()
    if any(w in topic_lower for w in ["wolfmed", "edtech", "education", "tutoring", "learning"]):
        return "edu-ai"
    if any(w in topic_lower for w in ["agenthire", "marketplace", "saas"]):
        return "saas-marketplace"
    if any(w in topic_lower for w in ["agent", "daemon", "wesa", "orchestration", "langchain", "crewai"]):
        return "agent-ecosystem"
    return "ai-frontier"


def _log_discard(topic: str, reason: str) -> None:
    print(f"[RT1] DISCARDED: {topic[:60]} — {reason}")
