"""
WESA Agent Ecosystem - Agent Daemon v0.8
Run this once and leave it running. It watches the tasks/ folder
and processes any new task files automatically.

Usage:
    python daemon.py

To submit a task: drop a .json file into the tasks/ folder.
Results appear in logs/ with matching task ID.
Axel's briefing is updated at: logs/AXEL_BRIEFING.md

Changes in v0.5:
- SQLite queue integrated: all task state is backed by logs/tasks.db
- sync_json_to_db() imports JSON tasks into SQLite on every loop
- update_briefing() now reads from SQLite (reliable, no JSON file scanning)
- output_file paths resolved relative to workspace root (not daemon cwd)
- Removed dead write_output_file() function

Changes in v0.6:
- PipelineController wired in: on_task_complete() called after every task
- StagingQueue wired in: Research Center briefs land in logs/staging.db
- coordinate tasks with START_PIPELINE: prompt trigger pipeline start

Changes in v0.7:
- Automatic retry with exponential backoff for transient API errors (503, 429, UNAVAILABLE)
- Retries up to 3 times with delays: 30s -> 60s -> 120s before marking failed

Changes in v0.8:
- ntfy bidirectional: make_ntfy_handler closure, dispatch_ntfy_command, pause/stop flag checks
- axel task type: runs axel_trigger.py headlessly for autonomous Axel orchestration
- execute_git_block: full per-command output in result, proper error detection for all git failures
"""

import os
import queue
import signal
import threading
import concurrent.futures
import time
import sys
import json
import uuid
import re
import yaml
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from src.task import Task, TaskStatus
from src.router import Router
from src.notifier import notify, notify_done, notify_failed, notify_started, start_ntfy_listener
from src.ntfy_commands import dispatch as dispatch_ntfy_command
from src.task_queue import TaskQueue
from src.pipeline_controller import PipelineController
from src.staging_queue import StagingQueue
from secure_core.control_plane import authorize_ntfy
from secure_core.vault import redact
from secure_core import AuditLog
from src.config import get_config
from src.logging_setup import configure_logging

_cfg = get_config()

# E0.6: structured JSON logging (logs/structured.jsonl) alongside the
# existing print()-based AXEL_BRIEFING/AXEL_LOG rendering pipeline, which
# is left untouched (CLAUDE.md Notification Loop). Migrating individual
# print() call sites to logger.info(..., extra={...}) is follow-up work.
configure_logging()

# Directories (relative to cwd - run daemon from agent-ecosystem/)
TASKS_DIR = Path("tasks")
LOGS_DIR = Path("logs")
LOGS_DESIGNS_DIR = LOGS_DIR / "designs"
LOGS_RESEARCH_DIR = LOGS_DIR / "research"
LOGS_DEV_DIR = LOGS_DIR / "dev"
BRIEFING_FILE = LOGS_DIR / "AXEL_BRIEFING.md"
POLL_INTERVAL = 3
# E0: these were os.environ.get() reads scattered at module scope — now
# sourced from the single validated WesaConfig (src/config.py).
MAX_WORKERS = _cfg.daemon_max_workers
RC_INTERVAL_HOURS = _cfg.rc_interval_hours
RC_SCHEDULE_FILE = LOGS_DIR / "rc_schedule.json"
RC_PIPELINE_NAME = "research-center"
RC_ENABLED_FILE = LOGS_DIR / "rc_enabled.flag"  # exists = enabled, absent = disabled
SC1_INTERVAL_HOURS = _cfg.sc1_interval_hours
SC1_PIPELINE_NAME = "sc1-scout"
SY1_INTERVAL_HOURS = _cfg.sy1_interval_hours
SY1_PIPELINE_NAME = "sy1-synthesis"
SC1_SCHEDULE_FILE = LOGS_DIR / "sc1_schedule.json"
SY1_SCHEDULE_FILE = LOGS_DIR / "sy1_schedule.json"

# Route task type → log subfolder
TASK_TYPE_LOG_DIR = {
    "design":       LOGS_DESIGNS_DIR,
    "frontend":     LOGS_DESIGNS_DIR,
    "review":       LOGS_DESIGNS_DIR,
    "code_review":  LOGS_DEV_DIR,
    "skill_update": LOGS_DESIGNS_DIR,
    "skill_merge":  LOGS_DESIGNS_DIR,
    "research":     LOGS_RESEARCH_DIR,
    "analyze":      LOGS_RESEARCH_DIR,
    "code":         LOGS_DEV_DIR,
    "write":        LOGS_DEV_DIR,
    "test":         LOGS_DEV_DIR,
    "assets":       LOGS_DEV_DIR,
    "git":          LOGS_DEV_DIR,
    "general":      LOGS_DEV_DIR,
    "axel":         LOGS_DEV_DIR,
    # Wolfmed extraction pipeline
    "extract_pdf":         LOGS_DEV_DIR,
    "validate_questions":  LOGS_DEV_DIR,
}

# Git repo root: the agent-ecosystem folder (where daemon.py lives)
REPO_DIR = Path(__file__).parent

# Workspace root: three levels up from daemon.py = WesaTeam/
WORKSPACE_ROOT = Path(__file__).resolve().parent
print(f"[DEBUG] WORKSPACE_ROOT = {WORKSPACE_ROOT}")

# Shared lock for all state writes (SQLite, pipeline_ctrl, briefing file).
# Held only during short DB/file update windows — never during agent.run().
_state_lock = threading.Lock()


def setup_dirs():
    TASKS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    LOGS_DESIGNS_DIR.mkdir(exist_ok=True)
    LOGS_RESEARCH_DIR.mkdir(exist_ok=True)
    LOGS_DEV_DIR.mkdir(exist_ok=True)


def write_pid():
    (LOGS_DIR / "daemon.pid").write_text(str(os.getpid()))


def _handle_sigterm(sig, frame):
    log("SIGTERM received. Daemon stopping cleanly.")
    sys.exit(0)


def log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

def _try_load_raw(task_path: Path) -> dict:
    """Load raw task JSON without Task class parsing (to read _rt1_meta etc.)"""
    try:
        return json.loads(task_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
 
def sync_json_to_db(queue: TaskQueue):
    """Import any JSON task files not yet tracked in SQLite."""
    for f in TASKS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id"):
                queue.import_task(data)
        except Exception as e:
            log(f"sync_json_to_db: failed on {f.name}: {e}")
            
def _get_pipeline_name_for_task(task_id: str, pipeline_ctrl: PipelineController) -> str:
    """Return the pipeline name for a given task_id, or '' if not found."""
    for f in pipeline_ctrl.running_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("current_task_id") == task_id or task_id.startswith(data.get("run_id", "___")):
                return data.get("pipeline", "")
        except Exception:
            pass
    return ""

def update_briefing(queue: TaskQueue):
    """Rebuild Axel's briefing file from SQLite. Called after every task completes."""
    tasks = queue.get_all()

    done        = [t for t in tasks if t.get("status") == "done"]
    failed      = [t for t in tasks if t.get("status") == "failed"]
    pending     = [t for t in tasks if t.get("status") == "pending"]
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]

    lines = [
        "# Axel Briefing",
        f"**Last updated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "**Read this at the start of every Cowork session to restore context.**",
        "",
        "---",
        "",
        "## Task Summary",
        f"- Done: {len(done)}",
        f"- Failed: {len(failed)}",
        f"- Pending: {len(pending)}",
        f"- In Progress: {len(in_progress)}",
        "",
    ]

    if failed:
        lines += ["## Needs Attention (Failed Tasks)", ""]
        for t in failed:
            lines.append(f"- **{t['id']}** [{t['type']}] — {(t.get('error') or 'unknown error')[:120]}")
        lines.append("")

    if pending:
        lines += ["## Pending Tasks", ""]
        for t in pending:
            lines.append(f"- **{t['id']}** [{t['type']}]")
        lines.append("")

    if done:
        lines += ["## Recently Completed", ""]
        for t in done[:10]:
            completed = (t.get("completed_at") or "")[:19].replace("T", " ")
            lines.append(f"- **{t['id']}** [{t['type']}] via {t.get('model') or '?'} at {completed}")
        lines.append("")

    lines += [
        "---",
        "",
        "## How to Use",
        "- Check 'Needs Attention' first - failed tasks may need retrying or investigation",
        "- Check 'Pending' - tasks waiting to be picked up",
        "- Full reports in: `logs/<task-id>_report.md`",
    ]

    BRIEFING_FILE.write_text("\n".join(lines), encoding="utf-8")


def write_report(task: Task):
    log_dir = TASK_TYPE_LOG_DIR.get(str(task.type).replace("TaskType.", ""), LOGS_DEV_DIR)
    report_path = log_dir / f"{task.id}_report.md"
    report = f"""# Task Report
**ID**: {task.id}
**Type**: {task.type}
**Status**: {task.status}
**Model**: {task.model_used}
**Created**: {task.created_at}
**Completed**: {task.completed_at}

## Prompt
{task.prompt}

## Result
{task.result if task.result else f"ERROR: {task.error}"}
"""
    report_path.write_text(report, encoding="utf-8")
    return report_path


def strip_code_fences(text: str) -> str:
    match = re.search(r'```(?:\w+)?\n(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1).rstrip()
    return text.strip()


# --- Git context enrichment (v0.8) ---

def _run_git(args: list, cwd: Path, max_lines: int = 300) -> str:
    """Run a git command, return stdout capped at max_lines."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        output = (result.stdout or "").strip()
        if not output:
            return (result.stderr or "").strip() or "(no output)"
        lines = output.splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n... [{len(lines) - max_lines} lines truncated]"
        return output
    except FileNotFoundError:
        return "(git not found in PATH)"
    except Exception as e:
        return f"(git command failed: {e})"


_GIT_ERROR_MARKERS = ("error:", "fatal:", "pathspec did not match", "not a git repo", "not found in PATH")

def execute_git_block(result: str, cwd: Path) -> tuple[bool, str]:
    """
    Parse G1's output for git add/commit lines and execute them.
    Handles both bare lines and lines inside markdown code fences.
    Returns (success, summary_message) where summary includes per-command output.
    """
    # Strip markdown code fences so we can scan inside them too
    clean = re.sub(r'```[^\n]*\n', '', result)
    clean = clean.replace('```', '')
    lines = [l.strip() for l in clean.splitlines()]
    commands = [
        l for l in lines
        if l.startswith("git add ") or l.startswith("git commit -m ")
    ]

    if not commands:
        return False, "No git commands found in G1 output — nothing executed."

    log_lines = []
    for cmd in commands:
        args = cmd[len("git "):].strip()  # everything after "git "
        # Build proper args list
        if args.startswith("add "):
            git_args = ["add", args[4:].strip()]
        elif args.startswith("commit -m "):
            # Extract the message — may be quoted
            msg_raw = args[len("commit -m "):].strip().strip('"').strip("'")
            git_args = ["commit", "-m", msg_raw]
        else:
            continue

        out = _run_git(git_args, cwd)
        cmd_label = f"git {' '.join(git_args[:2])}"
        log(f"[G1 exec] {cmd_label} → {out[:120]}")
        log_lines.append(f"{cmd_label}:\n  {out[:300]}")

        # Check for errors on any command
        out_lower = out.lower()
        if any(marker in out_lower for marker in _GIT_ERROR_MARKERS):
            summary = f"FAILED at '{cmd_label}':\n  {out}\n\nPrevious commands:\n" + "\n".join(log_lines[:-1]) if len(log_lines) > 1 else f"FAILED: {out}"
            return False, summary

        # Commit-specific checks
        if git_args[0] == "commit":
            if "nothing to commit" in out_lower:
                return False, f"Commit skipped — nothing staged.\n\n" + "\n".join(log_lines)
            if "author identity unknown" in out_lower or "please tell me who you are" in out_lower:
                return False, f"Commit failed — git user.name/user.email not configured.\nRun: git config --global user.email / user.name\n\n{out}"

    if not log_lines:
        return False, "No valid git commands parsed."

    return True, "Git commands executed:\n\n" + "\n\n".join(log_lines)


def enrich_git_prompt(task: Task) -> None:
    """
    Auto-inject live git context into a git task prompt before routing to G1.
    G1 receives git status + diff + recent log automatically — Greg just submits
    an empty prompt or a brief note, no need to copy-paste diffs manually.
    """
    status  = _run_git(["status", "--short"], REPO_DIR)
    diff    = _run_git(["diff", "HEAD", "--", "."], REPO_DIR, max_lines=400)
    recent  = _run_git(["log", "--oneline", "-5"], REPO_DIR)

    context = (
        f"=== AUTO-INJECTED GIT CONTEXT ({REPO_DIR.name}) ===\n\n"
        f"### git status --short\n{status}\n\n"
        f"### git log --oneline -5\n{recent}\n\n"
        f"### git diff HEAD\n{diff}\n\n"
        f"=== END GIT CONTEXT ===\n\n"
    )

    notes = task.prompt.strip()
    task.prompt = context + (
        f"### Notes from Greg:\n{notes}" if notes
        else "### Notes from Greg:\n(none — analyze the diff and generate the commit block)"
    )


# --- Retry logic (v0.7) ---

RETRY_DELAYS = [30, 60, 120]          # seconds between retries for API errors (429, 503, etc.)
TIMEOUT_RETRY_DELAYS = [120, 300, 600]  # longer waits for timeouts — retrying after 30s is pointless
RETRYABLE_TASK_TYPES = {"research", "analyze", "design"}  # task types eligible for auto-requeue on failure
MAX_AUTO_RETRIES = 2          # max auto-requeues per original task
RETRY_AFTER_MINUTES = 5       # minutes to wait before auto-retry fires


def is_timeout_error(e: Exception) -> bool:
    """Return True if the error is an agent-level timeout (timed out after Ns)."""
    return 'timed out after' in str(e).lower()


def is_transient_error(e: Exception) -> bool:
    """Return True if the error is transient and worth retrying (API errors + timeouts)."""
    msg = str(e).lower()
    return any(marker in msg for marker in [
        '503', '429', 'unavailable', 'deadline expired', 'high demand',
        'resource exhausted', 'service unavailable', 'timed out after'
    ])


    # NOTE: SU1 skill updates are now handled exclusively by the design-loop pipeline's
    # skill_update stage. Removed standalone queue_axel_skill_update() hook to prevent
    # double SU1 execution (pipeline stage + daemon hook both firing after every Q1 review).


def parse_pipeline_trigger(prompt: str):
    """
    Parse 'START_PIPELINE:<name> TOPIC:<topic>' from a coordinate task prompt.
    Returns (pipeline_name, topic) or (None, None) if format doesn't match.
    """
    match = re.match(r'START_PIPELINE:(\S+)\s+TOPIC:(.*)', prompt.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return None, None


def _load_rc_schedule() -> dict:
    if RC_SCHEDULE_FILE.exists():
        try:
            return json.loads(RC_SCHEDULE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"next_topic_index": 0, "last_triggered_at": None}


def _save_rc_schedule(state: dict):
    RC_SCHEDULE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


STALE_PIPELINE_HOURS = 2  # mark running pipelines as stale after this long


def _rc_pipeline_running(pipeline_ctrl: PipelineController) -> bool:
    """Return True if any research-center pipeline run is currently active (not stale)."""
    running_dir = pipeline_ctrl.running_dir
    for f in running_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("pipeline") != RC_PIPELINE_NAME or data.get("status") != "running":
                continue
            # Guard against stale state: if started more than STALE_PIPELINE_HOURS ago, close it
            started = data.get("started_at")
            if started:
                age = datetime.now() - datetime.fromisoformat(started)
                if age > timedelta(hours=STALE_PIPELINE_HOURS):
                    data["status"] = "done"
                    data["note"] = f"Auto-closed: stale running state (age {age})"
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    log(f"[RC] Closed stale pipeline run {f.stem} (age {age})")
                    continue
            return True
        except Exception:
            pass
    return False


def auto_run_research_center(pipeline_ctrl: PipelineController, queue: TaskQueue):
    if not RC_ENABLED_FILE.exists():
        return
 
    rc_yaml_path = pipeline_ctrl.pipelines_dir / f"{RC_PIPELINE_NAME}.yaml"
    if not rc_yaml_path.exists():
        return
 
    if _rc_pipeline_running(pipeline_ctrl):
        return
 
    schedule = _load_rc_schedule()
    last_run = schedule.get("last_triggered_at")
    if last_run:
        elapsed = datetime.now() - datetime.fromisoformat(last_run)
        if elapsed < timedelta(hours=RC_INTERVAL_HOURS):
            return
 
    # Try dynamic queue first
    from src.rc_queue_manager import load_queue, save_queue, next_topic
    rc_queue = load_queue()
    topic_entry = next_topic(rc_queue)
 
    if topic_entry:
        topic = topic_entry["title"]
        save_queue(rc_queue)
        log(f"[RC Auto] Queue topic: {topic[:60]} (score={topic_entry.get('composite_score', 0):.1f}, priority={topic_entry.get('priority')})")
    else:
        # Fallback: round-robin from static topics list in YAML
        try:
            with open(rc_yaml_path, "r", encoding="utf-8") as f:
                rc_def = yaml.safe_load(f)
        except Exception:
            return
        topics = rc_def.get("topics", [])
        if not topics:
            return
        idx = schedule.get("next_topic_index", 0) % len(topics)
        topic = topics[idx]
        schedule["next_topic_index"] = (idx + 1) % len(topics)
        log(f"[RC Auto] Queue empty — fallback topic {idx + 1}/{len(topics)}: {topic[:60]}")
 
    try:
        pipeline_ctrl.start_pipeline(RC_PIPELINE_NAME, queue, topic)
        schedule["last_triggered_at"] = datetime.now().isoformat()
        _save_rc_schedule(schedule)
    except Exception as e:
        log(f"[RC Auto] Failed to start pipeline: {e}")

def auto_run_scouts(pipeline_ctrl: PipelineController, queue: TaskQueue):
    """Fire SC1 scout every SC1_INTERVAL_HOURS and SY1 once daily if RC is enabled."""
    if not RC_ENABLED_FILE.exists():
        return
 
    def _should_run(schedule_file: Path, interval_hours: int) -> bool:
        if not schedule_file.exists():
            return True
        try:
            data = json.loads(schedule_file.read_text(encoding="utf-8"))
            last = data.get("last_run")
            if last:
                return datetime.now() - datetime.fromisoformat(last) >= timedelta(hours=interval_hours)
        except Exception:
            pass
        return True
 
    def _mark_run(schedule_file: Path):
        schedule_file.write_text(
            json.dumps({"last_run": datetime.now().isoformat()}, indent=2), encoding="utf-8"
        )
 
    # SC1 scout
    if SC1_PIPELINE_NAME in pipeline_ctrl._pipelines and _should_run(SC1_SCHEDULE_FILE, SC1_INTERVAL_HOURS):
        # Check no SC1 run is already in flight
        sc1_running = any(
            json.loads(f.read_text(encoding="utf-8")).get("pipeline") == SC1_PIPELINE_NAME and
            json.loads(f.read_text(encoding="utf-8")).get("status") == "running"
            for f in pipeline_ctrl.running_dir.glob("*.json")
            if f.read_text(encoding="utf-8")
        )
        if not sc1_running:
            try:
                pipeline_ctrl.start_pipeline(SC1_PIPELINE_NAME, queue, "")
                _mark_run(SC1_SCHEDULE_FILE)
                log(f"[SC1] Scout run started")
            except Exception as e:
                log(f"[SC1] Failed to start: {e}")
 
    # SY1 synthesis
    if SY1_PIPELINE_NAME in pipeline_ctrl._pipelines and _should_run(SY1_SCHEDULE_FILE, SY1_INTERVAL_HOURS):
        sy1_running = any(
            json.loads(f.read_text(encoding="utf-8")).get("pipeline") == SY1_PIPELINE_NAME and
            json.loads(f.read_text(encoding="utf-8")).get("status") == "running"
            for f in pipeline_ctrl.running_dir.glob("*.json")
            if f.read_text(encoding="utf-8")
        )
        if not sy1_running:
            try:
                pipeline_ctrl.start_pipeline(SY1_PIPELINE_NAME, queue, "")
                _mark_run(SY1_SCHEDULE_FILE)
                log(f"[SY1] Synthesis run started")
            except Exception as e:
                log(f"[SY1] Failed to start: {e}")


def append_pipeline_notification(completion: dict):
    """Append a pipeline completion notice to AXEL_BRIEFING after update_briefing() runs."""
    if not BRIEFING_FILE.exists():
        return
    stop_reason = completion.get("stop_reason", "completed")
    run_id = completion.get("run_id", "?")
    pipeline = completion.get("pipeline", "?")
    topic = completion.get("topic", "")
    iteration = completion.get("iteration", "?")
    overlay_path = completion.get("overlay_path", "")

    if stop_reason == "completed":
        headline = "APPROVED — Skill merge complete"
        detail = (
            f"Design approved after {iteration} iteration(s).\n"
            f"SU1 has permanently merged the run tips into `ux_agent.yaml`.\n"
            f"**Action required**: Review `WesaTeam/agent-ecosystem/agents/skills/ux_agent.yaml` "
            f"and correct any poor or product-specific rules SU1 may have introduced."
        )
    elif stop_reason == "max_iterations_reached":
        headline = "MAX ITERATIONS — No merge"
        detail = (
            f"Design loop ran all {iteration} iterations without reaching 72/80.\n"
            f"Review Q1 findings at `logs/designs/{run_id}_v*_review.md` to understand "
            f"what kept scoring low. Consider updating the R1 or design prompts."
        )
    else:
        headline = f"Pipeline ended — {stop_reason}"
        detail = "Check pipeline state file for details."

    notice = (
        f"\n---\n\n"
        f"## Pipeline Notification — {pipeline}\n\n"
        f"**Status**: {headline}\n"
        f"**Run ID**: `{run_id}`\n"
        f"**Topic**: {topic}\n"
        f"**Completed at**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{detail}\n\n"
        f"Overlay: `{overlay_path}`\n"
        f"Q1 Reviews: `logs/designs/{run_id}_v*_review.md`\n"
    )
    with open(BRIEFING_FILE, "a", encoding="utf-8") as f:
        f.write(notice)


def _claim_task(task_path: Path, queue: TaskQueue) -> "Task | None":
    """
    Atomically claim a pending task. Returns Task if claimed, None if already taken.
    Respects retry_after field — skips tasks that are not yet due.
    Must be called under _state_lock.
    """
    try:
        raw = json.loads(task_path.read_text(encoding="utf-8"))
        retry_after = raw.get("retry_after")
        if retry_after and datetime.now() < datetime.fromisoformat(retry_after):
            return None
    except Exception:
        pass
    task = Task.from_file(task_path)
    if task.status != TaskStatus.PENDING:
        return None
    task.status = TaskStatus.IN_PROGRESS
    # Preserve unknown fields like _rt1_meta when writing back
    data = json.loads(task_path.read_text(encoding="utf-8"))
    data.update(json.loads(task.to_json()))
    task_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    # Ensure task exists in SQLite before updating status — covers tasks
    # created on disk after the last sync_json_to_db() pass
    queue.import_task(data)  # ← ADD THIS LINE
    
    queue.update_status(task.id, "in_progress")
    return task


def _schedule_retry(task: Task, task_path: Path) -> bool:
    """
    Auto-requeue a failed task if eligible.
    Only fires for transient failures (timeouts, API errors) on retryable task types.
    Returns True if a retry was scheduled.
    """
    task_type_str = str(task.type).replace("TaskType.", "")
    if task_type_str not in RETRYABLE_TASK_TYPES:
        return False
    retry_count = task.id.count("_retry")
    if retry_count >= MAX_AUTO_RETRIES:
        log(f"[retry] {task.id} hit MAX_AUTO_RETRIES ({MAX_AUTO_RETRIES}) — no further auto-retry")
        return False
    retry_id = f"{task.id}_retry{retry_count + 1}"
    retry_after = (datetime.now() + timedelta(minutes=RETRY_AFTER_MINUTES)).isoformat()
    retry_task: dict = {
        "id": retry_id,
        "type": task_type_str,
        "prompt": task.prompt,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "retry_after": retry_after,
        "retry_of": task.id,
    }
    if task.output_file:
        retry_task["output_file"] = task.output_file
    retry_path = task_path.parent / f"{retry_id}.json"
    retry_path.write_text(json.dumps(retry_task, indent=2), encoding="utf-8")
    log(f"[retry] Scheduled {retry_id} in {RETRY_AFTER_MINUTES}min (attempt {retry_count + 1}/{MAX_AUTO_RETRIES})")
    return True


def process_task(task: Task, task_path: Path, router: Router, queue: TaskQueue, pipeline_ctrl: PipelineController, staging: StagingQueue):
    """Execute a pre-claimed task. Task is already IN_PROGRESS when this is called."""
    task_type_str = str(task.type).replace("TaskType.", "")

    # axel tasks: run axel_trigger.py headlessly — consumed here, not sent to a worker
    if task_type_str == "axel":
        message = task.prompt.strip() or "Axel, check inbox and proceed autonomously"
        log(f"[axel] Launching headless Axel session: {message[:80]}")
        try:
            trigger_script = Path(__file__).parent / "axel_trigger.py"
            result = subprocess.run(
                [sys.executable, str(trigger_script), message],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=660,
            )
            summary = f"exit={result.returncode} | {(result.stdout or '')[-300:]}"
            task.status = TaskStatus.DONE
            task.result = summary
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            summary = f"axel_trigger error: {e}"
        task.completed_at = datetime.now().isoformat()
        task.save(task_path)
        with _state_lock:
            if task.status == TaskStatus.DONE:
                queue.update_status(task.id, "done", result=task.result)
            else:
                queue.update_status(task.id, "failed", error=task.error)
            update_briefing(queue)
        log(f"[axel] Session complete: {summary[:120]}")
        return

    # coordinate + START_PIPELINE tasks are consumed here — not sent to a worker
    if task_type_str == "coordinate" and task.prompt.startswith("START_PIPELINE:"):
        pipeline_name, topic = parse_pipeline_trigger(task.prompt)
        if pipeline_name:
            log(f"Pipeline trigger: {pipeline_name} | topic: {topic}")
            try:
                with _state_lock:
                    run_id = pipeline_ctrl.start_pipeline(pipeline_name, queue, topic)
                log(f"Pipeline '{pipeline_name}' started — run_id: {run_id}")
            except Exception as e:
                log(f"Pipeline start failed: {e}")
        task.status = TaskStatus.DONE
        task.completed_at = datetime.now().isoformat()
        task.result = f"Pipeline trigger consumed: {task.prompt}"
        task.save(task_path)
        with _state_lock:
            queue.update_status(task.id, "done", result=task.result)
            update_briefing(queue)
        return

    if task_type_str == "analyze" and task.prompt.startswith("START_RT1:"):
        import json as _json
        from src.rc_queue_manager import handle_rt1_result
 
        meta = raw.get("_rt1_meta", {}) if (raw := _try_load_raw(task_path)) else {}
        topic = meta.get("topic", task.prompt)
        brief_content = meta.get("brief_content", "")
        raw_research = meta.get("raw_research", "")
 
        # Build RT1 prompt and run it through the router
        rt1_pipeline = pipeline_ctrl._pipelines.get("rt1-router")
        if rt1_pipeline:
            rt1_prompt = (
                rt1_pipeline["stages"][0]["prompt_template"]
                .replace("{topic}", topic)
                .replace("{prev_output}", brief_content)
            )
            # Resolve read_file placeholders
            import re as _re
            for m in _re.finditer(r"\{read_file:([^}]+)\}", rt1_prompt):
                rp = WORKSPACE_ROOT / m.group(1).strip()
                try:
                    rt1_prompt = rt1_prompt.replace(m.group(0), rp.read_text(encoding="utf-8"))
                except Exception:
                    rt1_prompt = rt1_prompt.replace(m.group(0), "[file not found]")
 
            rt1_task = Task(prompt=rt1_prompt, type=task.type)
            rt1_result, model_used = router.run(rt1_task)
        else:
            rt1_result = '{"decision":"AUTO-SAVE","composite_score":0,"reason":"rt1-router pipeline not loaded"}'
            model_used = "fallback"
 
        decision = handle_rt1_result(
            rt1_output=rt1_result,
            topic=topic,
            brief_content=brief_content,
            raw_research=raw_research,
            staging_queue=staging,
        )
 
        task.status = TaskStatus.DONE
        task.result = f"RT1 decision: {decision}\n\n{rt1_result}"
        task.model_used = model_used
        task.completed_at = datetime.now().isoformat()
        task.save(task_path)
        with _state_lock:
            queue.update_status(task.id, "done", result=task.result, model_used=model_used)
            update_briefing(queue)
        log(f"[RT1] {task.id} → {decision}")
        return

    log(f"Processing task {task.id} [{task.type}] ...")
    
    # Auto-enrich git tasks: inject live git status + diff before routing to G1
    if task_type_str == "git":
        log(f"Task {task.id} [git] — injecting live git context from {REPO_DIR.name}")
        enrich_git_prompt(task)
        task.save(task_path)  # persist enriched prompt to disk

    try:
        # Retry loop: up to 3 retries for transient errors (503, 429, UNAVAILABLE)
        # router.run() is intentionally outside _state_lock — it is the long part.
        result, model_used = None, None
        for attempt in range(len(RETRY_DELAYS) + 1):  # attempts: 0, 1, 2, 3
            try:
                result, model_used = router.run(task)
                break  # success — exit retry loop
            except Exception as e:
                if is_transient_error(e) and attempt < len(RETRY_DELAYS):
                    wait = TIMEOUT_RETRY_DELAYS[attempt] if is_timeout_error(e) else RETRY_DELAYS[attempt]
                    log(f"Task {task.id} transient error (attempt {attempt + 1}/3): {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise  # non-transient, or all retries exhausted — propagate to outer except

        task.result = result
        task.model_used = model_used
        task.status = TaskStatus.DONE
        task.completed_at = datetime.now().isoformat()
        task.save(task_path)

        write_report(task)

        if task.output_file:
            output_path = WORKSPACE_ROOT / task.output_file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(strip_code_fences(result), encoding="utf-8")
            log(f"Output written to {output_path}")
        elif task_type_str == "design":
            # Standalone design task (no output_file set) — auto-save to static/designs/
            # so it appears in the Designs tab alongside pipeline prototypes.
            auto_path = WORKSPACE_ROOT / "WesaTeam" / "agent-ecosystem" / "static" / "designs" / f"{task.id}_prototype.html"
            auto_path.parent.mkdir(parents=True, exist_ok=True)
            auto_path.write_text(strip_code_fences(result), encoding="utf-8")
            task.output_file = f"WesaTeam/agent-ecosystem/static/designs/{task.id}_prototype.html"
            task.save(task_path)

        with _state_lock:
            queue.update_status(task.id, "done", result=result, model_used=model_used)
            update_briefing(queue)

        log(f"Task {task.id} DONE via {model_used}")

        # Auto-execute git commands from G1 output
        if task_type_str == "git":
            success, summary = execute_git_block(result, REPO_DIR)
            log(f"[G1 exec] {'OK' if success else 'SKIPPED'}: {summary.splitlines()[0]}")
            task.result = result + f"\n\n---\n**Auto-executed**: {summary}"
            task.save(task_path)
            with _state_lock:
                queue.update_status(task.id, "done", result=task.result, model_used=model_used)

        notify_done(task.id, task.type, model_used)

        # Advance pipeline if this task belongs to one
        with _state_lock:
            completion = pipeline_ctrl.on_task_complete(task.id, result, queue)
            # Merge SC1/SY1 scout output into rc_queue
            pipeline_name_of_task = _get_pipeline_name_for_task(task.id, pipeline_ctrl)
            if pipeline_name_of_task in (SC1_PIPELINE_NAME, SY1_PIPELINE_NAME):
                from src.rc_queue_manager import load_queue, save_queue, merge_scout_results
                channel = "scout" if pipeline_name_of_task == SC1_PIPELINE_NAME else "synthesis"
                try:
                    rc_queue = load_queue()
                    added = merge_scout_results(result, channel, rc_queue)
                    save_queue(rc_queue)
                    log(f"[{channel.upper()}] Merged {added} topic(s) into rc_queue")
                except Exception as e:
                    log(f"[{channel.upper()}] merge_scout_results failed: {e}")
            if completion:
                append_pipeline_notification(completion)

    except Exception as e:
        task.error = str(e)
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now().isoformat()
        task.save(task_path)
        # Auto-requeue for transient failures (timeouts, API errors) on eligible task types
        if is_transient_error(e):
            _schedule_retry(task, task_path)
        with _state_lock:
            queue.update_status(task.id, "failed", error=str(e))
            write_report(task)
            update_briefing(queue)
        log(f"Task {task.id} FAILED: {e}")
        notify_failed(task.id, str(e))
        
def make_ntfy_handler(queue: TaskQueue, logs_dir: Path):
    """Return a closure that handles inbound ntfy messages with access to queue and logs_dir."""
    audit = AuditLog(logs_dir / "AUDIT.jsonl")

    def handle_ntfy_message(msg: dict) -> None:
        raw   = (msg.get("message") or "").strip()
        title = (msg.get("title")   or "").strip()
        # Ignore outbound daemon/axel-trigger notifications echoed back by ntfy
        # All daemon-sent bodies are prefixed [daemon], all axel-trigger bodies [axel-trigger]
        if "[daemon]" in raw or "[axel-trigger]" in raw:
            return

        # S0 control-plane lockdown: inbound ntfy commands must be prefixed
        # with the shared secret (secure_core.control_plane). The topic
        # itself is public/guessable, so this is the only gate. Unauthorized
        # messages are logged (redacted) to the audit chain and dropped —
        # no command dispatch, no task creation.
        authorized, raw = authorize_ntfy(raw)
        if not authorized:
            try:
                audit.append("ntfy", "unauthorized_message", title=redact(title), body=redact(raw))
            except Exception:
                pass
            log(f"[ntfy] REJECTED unauthorized message (title={title!r})")
            return

        # Try command dispatch first (status, pause, stop, help, approve, retry)
        if dispatch_ntfy_command(raw, queue, logs_dir, notify):
            return
        # Not a command — create a task
        if ":" in raw:
            prefix, _, rest = raw.partition(":")
            task_type = prefix.strip().lower()
            prompt    = rest.strip()
        else:
            task_type = "general"
            prompt    = raw
        task = {
            "id":         f"ntfy-{uuid.uuid4().hex[:8]}",
            "type":       task_type,
            "prompt":     prompt,
            "status":     "pending",
            "created_at": datetime.now().isoformat(),
        }
        task_path = TASKS_DIR / f"{task['id']}.json"
        task_path.write_text(json.dumps(task, indent=2), encoding="utf-8")
        log(f"[ntfy] Task {task['id']} [{task_type}]: {prompt[:60]}")
    return handle_ntfy_message

def run():
    setup_dirs()
    router = Router()
    queue = TaskQueue(db_path=str(LOGS_DIR / "tasks.db"))
    staging = StagingQueue(db_path=str(LOGS_DIR / "staging.db"))
    pipeline_ctrl = PipelineController(
        pipelines_dir=str(Path("agents/pipelines")),
        tasks_dir=str(TASKS_DIR),
        workspace_root=str(WORKSPACE_ROOT),
        staging_queue=staging,
    )

    log(f"=== WESA Agent Daemon v0.9 STARTED (max_workers={MAX_WORKERS}) ===")
    log(f"Watching:       {TASKS_DIR.resolve()}")
    log(f"Reports:        {LOGS_DIR.resolve()}")
    log(f"SQLite DB:      {(LOGS_DIR / 'tasks.db').resolve()}")
    log(f"Staging DB:     {(LOGS_DIR / 'staging.db').resolve()}")
    log(f"Briefing:       {BRIEFING_FILE.resolve()}")
    log(f"Workspace root: {WORKSPACE_ROOT}")
    log(f"Polling every {POLL_INTERVAL}s. Press Ctrl+C to stop.\n")

    signal.signal(signal.SIGTERM, _handle_sigterm)
    write_pid()
    notify_started()
    sync_json_to_db(queue)
    update_briefing(queue)
    
    ntfy_stop = threading.Event()
    ntfy_handler = make_ntfy_handler(queue, LOGS_DIR)
    start_ntfy_listener(ntfy_handler, ntfy_stop)

    futures: dict = {}  # Future -> task_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            try:
                # Check stop flag (ntfy 'stop' command)
                stop_flag = LOGS_DIR / "daemon_stop.flag"
                if stop_flag.exists():
                    stop_flag.unlink()
                    log("[ntfy-cmd] Stop flag detected. Daemon stopping — waiting for running tasks...")
                    break

                # Check pause flag (ntfy 'pause' command) — spin until flag removed
                pause_flag = LOGS_DIR / "daemon_pause.flag"
                if pause_flag.exists():
                    log("[ntfy-cmd] Pause flag detected. Sleeping...")
                    while pause_flag.exists():
                        time.sleep(POLL_INTERVAL)
                    log("[ntfy-cmd] Pause flag removed. Resuming.")

                sync_json_to_db(queue)
                try:
                    auto_run_research_center(pipeline_ctrl, queue)
                except Exception as e:
                    log(f"[RC Auto] Scheduler error: {e}")
                # Re-sync: pipeline/RC may have dropped new JSON files after the first sync
                sync_json_to_db(queue)
                
                try:
                    auto_run_scouts(pipeline_ctrl, queue)
                except Exception as e:
                    log(f"[Scout Auto] Scheduler error: {e}")
                # Re-sync: SC1/SY1 may have dropped new JSON files
                sync_json_to_db(queue)

                for task_file in sorted(TASKS_DIR.glob("*.json")):
                    with _state_lock:
                        task = _claim_task(task_file, queue)
                    if task is None:
                        continue
                    future = executor.submit(process_task, task, task_file, router, queue, pipeline_ctrl, staging)
                    futures[future] = task.id
                    log(f"[thread] Submitted {task.id} [{task.type}] (active: {len(futures)})")

                # Reap completed futures and surface any uncaught errors
                done_futures = [f for f in list(futures.keys()) if f.done()]
                for f in done_futures:
                    try:
                        f.result()
                    except Exception as e:
                        log(f"[thread] Uncaught error in task {futures[f]}: {e}")
                    del futures[f]

                time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                log("Daemon stopping — waiting for running tasks to finish...")
                break


if __name__ == "__main__":
    run()
