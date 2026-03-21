"""
WESA Agent Ecosystem - Agent Daemon v0.3
Run this once and leave it running. It watches the tasks/ folder
and processes any new task files automatically.

Usage:
    python daemon.py

To submit a task: drop a .json file into the tasks/ folder.
Results appear in logs/ with matching task ID.
Axel's briefing is updated at: logs/AXEL_BRIEFING.md
"""

import time
import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from src.task import Task, TaskStatus
from src.router import Router
from src.notifier import notify_done, notify_failed, notify_started

TASKS_DIR = Path("tasks")
LOGS_DIR = Path("logs")
BRIEFING_FILE = LOGS_DIR / "AXEL_BRIEFING.md"
POLL_INTERVAL = 3


def setup_dirs():
    TASKS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)


def log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")


def update_briefing():
    """Rebuild Axel's briefing file from all task files. Called after every task completes."""
    tasks = []
    for f in sorted(TASKS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            tasks.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass

    done = [t for t in tasks if t.get("status") == "done"]
    failed = [t for t in tasks if t.get("status") == "failed"]
    pending = [t for t in tasks if t.get("status") == "pending"]
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]

    lines = [
        "# Axel Briefing",
        f"**Last updated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Read this at the start of every Cowork session to restore context.**",
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
            lines.append(f"- **{t['id']}** [{t['type']}] — {t.get('error', 'unknown error')[:120]}")
        lines.append("")

    if pending:
        lines += ["## Pending Tasks", ""]
        for t in pending:
            lines.append(f"- **{t['id']}** [{t['type']}]")
        lines.append("")

    if done:
        lines += ["## Recently Completed", ""]
        for t in done[:10]:
            completed = t.get("completed_at", "")[:19].replace("T", " ")
            lines.append(f"- **{t['id']}** [{t['type']}] via {t.get('model_used', '?')} at {completed}")
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
    report_path = LOGS_DIR / f"{task.id}_report.md"
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


def process_task(task_path: Path, router: Router):
    task = Task.from_file(task_path)

    if task.status != TaskStatus.PENDING:
        return

    log(f"Processing task {task.id} [{task.type}] ...")
    task.status = TaskStatus.IN_PROGRESS
    task.save(task_path)

    try:
        result, model_used = router.run(task)
        task.result = result
        task.model_used = model_used
        task.status = TaskStatus.DONE
        task.completed_at = datetime.now().isoformat()
        task.save(task_path)
        write_report(task)
        update_briefing()
        log(f"Task {task.id} DONE via {model_used}")
        notify_done(task.id, task.type, model_used)
    except Exception as e:
        task.error = str(e)
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now().isoformat()
        task.save(task_path)
        write_report(task)
        update_briefing()
        log(f"Task {task.id} FAILED: {e}")
        notify_failed(task.id, str(e))


def run():
    setup_dirs()
    router = Router()

    log("=== WESA Agent Daemon v0.3 STARTED ===")
    log(f"Watching:  {TASKS_DIR.resolve()}")
    log(f"Reports:   {LOGS_DIR.resolve()}")
    log(f"Briefing:  {BRIEFING_FILE.resolve()}")
    log(f"Polling every {POLL_INTERVAL}s. Press Ctrl+C to stop.\n")
    notify_started()
    update_briefing()

    while True:
        try:
            for task_file in sorted(TASKS_DIR.glob("*.json")):
                try:
                    process_task(task_file, router)
                except Exception as e:
                    log(f"Unhandled error on {task_file.name}: {e}")
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log("Daemon stopped.")
            break


if __name__ == "__main__":
    run()
