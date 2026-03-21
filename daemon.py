"""
WESA Agent Ecosystem - Agent Daemon v0.1
Run this once and leave it running. It watches the tasks/ folder
and processes any new task files automatically.

Usage:
    python daemon.py

To submit a task: drop a .json file into the tasks/ folder.
Results appear in logs/ with matching task ID.
"""

import time
import sys
import json
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.task import Task, TaskStatus
from src.router import Router

TASKS_DIR = Path("tasks")
LOGS_DIR = Path("logs")
POLL_INTERVAL = 3  # seconds between scans


def setup_dirs():
    TASKS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)


def log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")


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

    # Mark in progress
    task.status = TaskStatus.IN_PROGRESS
    task.save(task_path)

    try:
        result, model_used = router.run(task)
        task.result = result
        task.model_used = model_used
        task.status = TaskStatus.DONE
        log(f"Task {task.id} DONE via {model_used}")
    except Exception as e:
        task.error = str(e)
        task.status = TaskStatus.FAILED
        log(f"Task {task.id} FAILED: {e}")

    task.completed_at = datetime.now().isoformat()
    task.save(task_path)

    report_path = write_report(task)
    log(f"Report saved → {report_path}")


def run():
    setup_dirs()
    router = Router()

    log("=== WESA Agent Daemon v0.1 STARTED ===")
    log(f"Watching: {TASKS_DIR.resolve()}")
    log(f"Reports:  {LOGS_DIR.resolve()}")
    log(f"Polling every {POLL_INTERVAL}s. Press Ctrl+C to stop.\n")

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
