#!/usr/bin/env python3
"""Requeue failed WESA daemon tasks.

Run this from agent-ecosystem/scripts, or pass --tasks-dir explicitly.
The script only changes tasks whose JSON status is exactly ``failed``.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def atomic_write(path: Path, data: dict) -> None:
    """Write a task safely so the daemon never sees half-written JSON."""
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_tasks_dir = script_dir.parent / "tasks"

    parser = argparse.ArgumentParser(
        description="Retry failed WESA daemon tasks by setting them back to pending."
    )
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=default_tasks_dir,
        help=f"Task directory (default: {default_tasks_dir})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List failed tasks without changing anything.",
    )
    parser.add_argument(
        "--type",
        choices=("extract_pdf", "generate_from_slides", "validate_questions"),
        help="Retry only tasks of this type.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        help="Retry only this task ID; may be specified more than once.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tasks_dir = args.tasks_dir.expanduser().resolve()

    if not tasks_dir.is_dir():
        print(f"Task directory not found: {tasks_dir}")
        return 2

    selected_ids = set(args.task_id or [])
    found = retried = skipped_invalid = 0

    for task_path in sorted(tasks_dir.glob("*.json")):
        try:
            with task_path.open(encoding="utf-8") as handle:
                task = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            skipped_invalid += 1
            print(f"Skipping unreadable task {task_path.name}: {exc}")
            continue

        task_id = task.get("id", task_path.stem)
        if task.get("status") != "failed":
            continue
        if args.type and task.get("type") != args.type:
            continue
        if selected_ids and task_id not in selected_ids:
            continue

        found += 1
        task_type = task.get("type", "unknown")
        old_error = task.get("error", "")
        print(f"{'Would retry' if args.dry_run else 'Retrying'} {task_id} ({task_type})")

        if args.dry_run:
            continue

        history = task.setdefault("retry_history", [])
        history.append(
            {
                "retried_at": datetime.now(timezone.utc).isoformat(),
                "previous_error": old_error,
            }
        )
        task["status"] = "pending"
        task["error"] = ""
        task["result"] = ""
        task["completed_at"] = ""
        atomic_write(task_path, task)
        retried += 1

    if not found:
        print("No matching failed tasks found.")
    elif args.dry_run:
        print(f"Dry run complete: {found} task(s) would be retried.")
    else:
        print(f"Requeued {retried} failed task(s). The daemon should pick them up shortly.")

    if skipped_invalid:
        print(f"Skipped {skipped_invalid} unreadable task file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
