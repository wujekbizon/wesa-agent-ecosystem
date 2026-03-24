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

Changes in v0.6:
- PipelineController wired in: on_task_complete() called after every task
- StagingQueue wired in: Research Center briefs land in logs/staging.db
- coordinate tasks with START_PIPELINE: prompt trigger pipeline start

Changes in v0.7:
- Automatic retry with exponential backoff for transient API errors (503, 429, UNAVAILABLE)
- Retries up to 3 times with delays: 30s -> 60s -> 120s before marking failed
"""

import os
import signal
import time
import sys
import json
import uuid
import re
import yaml
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from src.task import Task, TaskStatus
from src.router import Router
from src.notifier import notify_done, notify_failed, notify_started
from src.task_queue import TaskQueue
from src.pipeline_controller import PipelineController
from src.staging_queue import StagingQueue

# Directories (relative to cwd - run daemon from agent-ecosystem/)
TASKS_DIR = Path("tasks")
LOGS_DIR = Path("logs")
LOGS_DESIGNS_DIR = LOGS_DIR / "designs"
LOGS_RESEARCH_DIR = LOGS_DIR / "research"
LOGS_DEV_DIR = LOGS_DIR / "dev"
BRIEFING_FILE = LOGS_DIR / "AXEL_BRIEFING.md"
POLL_INTERVAL = 3
RC_INTERVAL_HOURS = int(os.environ.get("RC_INTERVAL_HOURS", "4"))
RC_SCHEDULE_FILE = LOGS_DIR / "rc_schedule.json"
RC_PIPELINE_NAME = "research-center"

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
    "general":      LOGS_DEV_DIR,
}

# Workspace root: three levels up from daemon.py = Coworker/
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


# --- Retry logic (v0.7) ---

RETRY_DELAYS = [30, 60, 120]  # seconds between retry attempts


def is_transient_error(e: Exception) -> bool:
    """Return True if the error is a transient API error worth retrying."""
    msg = str(e).lower()
    return any(marker in msg for marker in [
        '503', '429', 'unavailable', 'deadline expired', 'high demand',
        'resource exhausted', 'service unavailable'
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
    """
    Called on every daemon poll. Fires the next RC topic when:
    - No RC pipeline is currently running
    - At least RC_INTERVAL_HOURS have passed since last trigger
    """
    # Load RC pipeline definition to get topics list
    rc_yaml_path = pipeline_ctrl.pipelines_dir / f"{RC_PIPELINE_NAME}.yaml"
    if not rc_yaml_path.exists():
        return
    try:
        with open(rc_yaml_path, "r", encoding="utf-8") as f:
            rc_def = yaml.safe_load(f)
    except Exception:
        return

    topics = rc_def.get("topics", [])
    if not topics:
        return

    if _rc_pipeline_running(pipeline_ctrl):
        return

    schedule = _load_rc_schedule()
    last_run = schedule.get("last_triggered_at")
    if last_run:
        elapsed = datetime.now() - datetime.fromisoformat(last_run)
        if elapsed < timedelta(hours=RC_INTERVAL_HOURS):
            return

    # Pick next topic (round-robin)
    idx = schedule.get("next_topic_index", 0) % len(topics)
    topic = topics[idx]

    log(f"[RC Auto] Triggering research-center — topic {idx + 1}/{len(topics)}: {topic[:60]}...")
    try:
        pipeline_ctrl.start_pipeline(RC_PIPELINE_NAME, queue, topic)
        schedule["next_topic_index"] = (idx + 1) % len(topics)
        schedule["last_triggered_at"] = datetime.now().isoformat()
        _save_rc_schedule(schedule)
    except Exception as e:
        log(f"[RC Auto] Failed to start pipeline: {e}")


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


def process_task(task_path: Path, router: Router, queue: TaskQueue, pipeline_ctrl: PipelineController):
    task = Task.from_file(task_path)

    if task.status != TaskStatus.PENDING:
        return

    # coordinate + START_PIPELINE tasks are consumed here — not sent to a worker
    task_type_str = str(task.type).replace("TaskType.", "")
    if task_type_str == "coordinate" and task.prompt.startswith("START_PIPELINE:"):
        pipeline_name, topic = parse_pipeline_trigger(task.prompt)
        if pipeline_name:
            log(f"Pipeline trigger: {pipeline_name} | topic: {topic}")
            try:
                run_id = pipeline_ctrl.start_pipeline(pipeline_name, queue, topic)
                log(f"Pipeline '{pipeline_name}' started — run_id: {run_id}")
            except Exception as e:
                log(f"Pipeline start failed: {e}")
        task.status = TaskStatus.DONE
        task.completed_at = datetime.now().isoformat()
        task.result = f"Pipeline trigger consumed: {task.prompt}"
        task.save(task_path)
        queue.update_status(task.id, "done", result=task.result)
        update_briefing(queue)
        return

    log(f"Processing task {task.id} [{task.type}] ...")
    task.status = TaskStatus.IN_PROGRESS
    task.save(task_path)
    queue.update_status(task.id, "in_progress")

    try:
        # Retry loop: up to 3 retries for transient errors (503, 429, UNAVAILABLE)
        result, model_used = None, None
        for attempt in range(len(RETRY_DELAYS) + 1):  # attempts: 0, 1, 2, 3
            try:
                result, model_used = router.run(task)
                break  # success — exit retry loop
            except Exception as e:
                if is_transient_error(e) and attempt < len(RETRY_DELAYS):
                    wait = RETRY_DELAYS[attempt]
                    log(f"Task {task.id} transient error (attempt {attempt + 1}/3): {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise  # non-transient, or all retries exhausted — propagate to outer except

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

        # Advance pipeline if this task belongs to one
        completion = pipeline_ctrl.on_task_complete(task.id, result, queue)

        # If a pipeline just finished and requested notification, append to AXEL_BRIEFING
        if completion:
            append_pipeline_notification(completion)

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
    staging = StagingQueue(db_path=str(LOGS_DIR / "staging.db"))
    pipeline_ctrl = PipelineController(
        pipelines_dir=str(Path("agents/pipelines")),
        tasks_dir=str(TASKS_DIR),
        workspace_root=str(WORKSPACE_ROOT),
        staging_queue=staging,
    )

    log("=== WESA Agent Daemon v0.7 STARTED ===")
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

    while True:
        try:
            sync_json_to_db(queue)
            try:
                auto_run_research_center(pipeline_ctrl, queue)
            except Exception as e:
                log(f"[RC Auto] Scheduler error: {e}")
            for task_file in sorted(TASKS_DIR.glob("*.json")):
                try:
                    process_task(task_file, router, queue, pipeline_ctrl)
                except Exception as e:
                    log(f"Unhandled error on {task_file.name}: {e}")
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log("Daemon stopped.")
            break


if __name__ == "__main__":
    run()
