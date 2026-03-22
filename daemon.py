"""
WESA Agent Ecosystem - Agent Daemon v0.5
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
"""

import os
import signal
import time
import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from src.task import Task, TaskStatus
from src.router import Router
from src.notifier import notify_done, notify_failed, notify_started
from src.task_queue import TaskQueue

# Directories (relative to cwd - run daemon from agent-ecosystem/)
TASKS_DIR = Path("tasks")
LOGS_DIR = Path("logs")
LOGS_DESIGNS_DIR = LOGS_DIR / "designs"
LOGS_RESEARCH_DIR = LOGS_DIR / "research"
LOGS_DEV_DIR = LOGS_DIR / "dev"
BRIEFING_FILE = LOGS_DIR / "AXEL_BRIEFING.md"
POLL_INTERVAL = 3

# Route task type → log subfolder
TASK_TYPE_LOG_DIR = {
    "design":   LOGS_DESIGNS_DIR,
    "frontend": LOGS_DESIGNS_DIR,
    "review":   LOGS_DESIGNS_DIR,
    "research": LOGS_RESEARCH_DIR,
    "analyze":  LOGS_RESEARCH_DIR,
    "code":     LOGS_DEV_DIR,
    "write":    LOGS_DEV_DIR,
    "test":     LOGS_DEV_DIR,
    "assets":   LOGS_DEV_DIR,
    "general":  LOGS_DEV_DIR,
}

# Workspace root: three levels up from daemon.py = Coworker/
# Used to resolve output_file paths correctly regardless of cwd
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent


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


def sync_json_to_db(queue: TaskQueue):
    """Import any JSON task files not yet tracked in SQLite."""
    for f in TASKS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id"):
                queue.import_task(data)
        except Exception as e:
            log(f"sync_json_to_db: failed on {f.name}: {e}")


def update_briefing(queue: TaskQueue):
    """Rebuild Axel's briefing file from SQLite. Called after every task completes."""
    tasks = queue.get_all()

    done = [t for t in tasks if t.get("status") == "done"]
    failed = [t for t in tasks if t.get("status") == "failed"]
    pending = [t for t in tasks if t.get("status") == "pending"]
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
    """
    Extract code from agent output for writing to output_file.
    Handles two cases:
    - DevAgent style: full markdown response with explanatory text + ```lang ... ``` block
    - Simple style: output is just a code fence with no surrounding prose
    Always extracts the first code block found. Falls back to full text if none found.
    """
    # Case 1: response contains a fenced code block somewhere inside it
    # Find first ``` marker (with or without language tag)
    import re
    match = re.search(r'```(?:\w+)?\n(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1).rstrip()
    # Case 2: no fenced block - return as-is (write tasks, plain text output)
    return text.strip()


def process_task(task_path: Path, router: Router, queue: TaskQueue):
    task = Task.from_file(task_path)

    if task.status != TaskStatus.PENDING:
        return

    log(f"Processing task {task.id} [{task.type}] ...")
    task.status = TaskStatus.IN_PROGRESS
    task.save(task_path)
    queue.update_status(task.id, "in_progress")

    try:
        result, model_used = router.run(task)
        task.result = result
        task.model_used = model_used
        task.status = TaskStatus.DONE
        task.completed_at = datetime.now().isoformat()
        task.save(task_path)
        queue.update_status(task.id, "done", result=result, model_used=model_used)

        write_report(task)

        if task.output_file:
            output_path = WORKSPACE_ROOT / task.output_file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(strip_code_fences(result), encoding="utf-8")
            log(f"Output written to {output_path}")

        update_briefing(queue)
        log(f"Task {task.id} DONE via {model_used}")
        notify_done(task.id, task.type, model_used)

    except Exception as e:
        task.error = str(e)
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now().isoformat()
        task.save(task_path)
        queue.update_status(task.id, "failed", error=str(e))
        write_report(task)
        update_briefing(queue)
        log(f"Task {task.id} FAILED: {e}")
        notify_failed(task.id, str(e))


def run():
    setup_dirs()
    router = Router()
    queue = TaskQueue(db_path=str(LOGS_DIR / "tasks.db"))

    log("=== WESA Agent Daemon v0.5 STARTED ===")
    log(f"Watching:       {TASKS_DIR.resolve()}")
    log(f"Reports:        {LOGS_DIR.resolve()}")
    log(f"SQLite DB:      {(LOGS_DIR / 'tasks.db').resolve()}")
    log(f"Briefing:       {BRIEFING_FILE.resolve()}")
    log(f"Workspace root: {WORKSPACE_ROOT}")
    log(f"Polling every {POLL_INTERVAL}s. Press Ctrl+C to stop.\n")

    signal.signal(signal.SIGTERM, _handle_sigterm)
    write_pid()
    notify_started()
    sync_json_to_db(queue)
    update_briefing(queue)

    while True:
        try:
            sync_json_to_db(queue)
            for task_file in sorted(TASKS_DIR.glob("*.json")):
                try:
                    process_task(task_file, router, queue)
                except Exception as e:
                    log(f"Unhandled error on {task_file.name}: {e}")
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log("Daemon stopped.")
            break


if __name__ == "__main__":
    run()
