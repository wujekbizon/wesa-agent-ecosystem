"""
WESA Agent Ecosystem - CLI Monitor
Live terminal dashboard showing daemon status, task queue, and recent results.

Usage:
    python monitor.py          # refresh every 3s
    python monitor.py --once   # print once and exit
"""

import sys
import time
import json
import argparse
from pathlib import Path
from datetime import datetime

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.text import Text
    from rich import box
    from rich.live import Live
    from rich.layout import Layout
except ImportError:
    print("Install rich: pip install rich")
    sys.exit(1)

TASKS_DIR = Path("tasks")
LOGS_DIR = Path("logs")
REFRESH_SECONDS = 3
console = Console()


def load_tasks() -> list[dict]:
    tasks = []
    if not TASKS_DIR.exists():
        return tasks
    for f in sorted(TASKS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            tasks.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return tasks


def load_recent_logs(limit: int = 5) -> list[str]:
    if not LOGS_DIR.exists():
        return []
    reports = sorted(LOGS_DIR.glob("*_report.md"), key=lambda x: x.stat().st_mtime, reverse=True)
    return [f.name for f in reports[:limit]]


def status_color(status: str) -> str:
    return {
        "pending": "yellow",
        "in_progress": "cyan",
        "done": "green",
        "failed": "red",
    }.get(status, "white")


def build_dashboard() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="tasks"),
        Layout(name="logs"),
    )

    # Header
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    layout["header"].update(
        Panel(f"[bold cyan]WESA Agent Ecosystem Monitor[/bold cyan]   [dim]{now}[/dim]", box=box.HORIZONTALS)
    )

    # Task queue table
    tasks = load_tasks()
    task_table = Table(box=box.SIMPLE, show_header=True, header_style="bold white")
    task_table.add_column("ID", style="dim", width=10)
    task_table.add_column("Type", width=10)
    task_table.add_column("Status", width=12)
    task_table.add_column("Model", width=20)
    task_table.add_column("Completed", width=20)

    for t in tasks[:15]:
        status = t.get("status", "unknown")
        completed = t.get("completed_at", "")[:19].replace("T", " ") if t.get("completed_at") else "-"
        task_table.add_row(
            t.get("id", "-"),
            t.get("type", "-"),
            Text(status, style=status_color(status)),
            t.get("model_used", "-") or "-",
            completed,
        )

    if not tasks:
        task_table.add_row("-", "-", Text("No tasks yet", style="dim"), "-", "-")

    layout["tasks"].update(Panel(task_table, title=f"[bold]Task Queue[/bold] ({len(tasks)} tasks)", border_style="blue"))

    # Recent logs
    logs = load_recent_logs()
    log_text = "\n".join(f"[green]{l}[/green]" for l in logs) if logs else "[dim]No reports yet[/dim]"
    layout["logs"].update(Panel(log_text, title="[bold]Recent Reports[/bold]", border_style="blue"))

    # Footer stats
    done = sum(1 for t in tasks if t.get("status") == "done")
    failed = sum(1 for t in tasks if t.get("status") == "failed")
    pending = sum(1 for t in tasks if t.get("status") == "pending")
    in_progress = sum(1 for t in tasks if t.get("status") == "in_progress")

    stats = (
        f"[green]Done: {done}[/green]  "
        f"[cyan]In Progress: {in_progress}[/cyan]  "
        f"[yellow]Pending: {pending}[/yellow]  "
        f"[red]Failed: {failed}[/red]  "
        f"  [dim]Refresh: {REFRESH_SECONDS}s | Ctrl+C to exit[/dim]"
    )
    layout["footer"].update(Panel(stats, box=box.HORIZONTALS))

    return layout


def run_once():
    console.print(build_dashboard())


def run_live():
    with Live(build_dashboard(), refresh_per_second=1, screen=True) as live:
        try:
            while True:
                time.sleep(REFRESH_SECONDS)
                live.update(build_dashboard())
        except KeyboardInterrupt:
            pass
    console.print("[dim]Monitor stopped.[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WESA Agent Ecosystem Monitor")
    parser.add_argument("--once", action="store_true", help="Print once and exit")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_live()
