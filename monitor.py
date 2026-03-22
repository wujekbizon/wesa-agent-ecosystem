"""
WESA Agent Ecosystem - CLI Monitor v0.3
Live terminal dashboard with task details, error reports, and agent activity log.

Usage:
    python monitor.py          # live, refresh every 3s
    python monitor.py --once   # print once and exit
    python monitor.py --log    # full scrollable AXEL_LOG view
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
    from rich.text import Text
    from rich.layout import Layout
    from rich.live import Live
    from rich import box
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


def read_report(task_id: str) -> str:
    report_path = LOGS_DIR / f"{task_id}_report.md"
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")
    return ""


def status_style(status: str) -> str:
    return {
        "pending": "yellow",
        "in_progress": "bold cyan",
        "done": "green",
        "failed": "bold red",
    }.get(status, "white")


def truncate(text: str, length: int) -> str:
    return text[:length] + "..." if len(text) > length else text


def build_header() -> Panel:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return Panel(
        f"[bold cyan]WESA Agent Ecosystem[/bold cyan]  |  [bold]Axel's Monitor v0.2[/bold]  |  [dim]{now}[/dim]",
        box=box.HORIZONTALS,
        style="on grey7",
    )


def build_task_table(tasks: list[dict]) -> Panel:
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold white", expand=True)
    table.add_column("ID", style="dim", width=14)
    table.add_column("Type", width=10)
    table.add_column("Status", width=13)
    table.add_column("Agent / Model", width=28)
    table.add_column("Completed", width=19)

    for t in tasks[:20]:
        status = t.get("status", "unknown")
        completed = t.get("completed_at", "")[:19].replace("T", " ") if t.get("completed_at") else "-"
        table.add_row(
            t.get("id", "-"),
            t.get("type", "-"),
            Text(status, style=status_style(status)),
            t.get("model_used", "-") or "-",
            completed,
        )

    if not tasks:
        table.add_row("-", "-", Text("No tasks yet", style="dim"), "-", "-")

    done = sum(1 for t in tasks if t.get("status") == "done")
    failed = sum(1 for t in tasks if t.get("status") == "failed")
    pending = sum(1 for t in tasks if t.get("status") == "pending")
    in_prog = sum(1 for t in tasks if t.get("status") == "in_progress")

    title = (
        f"[bold]Tasks[/bold]  "
        f"[green]Done:{done}[/green]  "
        f"[cyan]Active:{in_prog}[/cyan]  "
        f"[yellow]Pending:{pending}[/yellow]  "
        f"[red]Failed:{failed}[/red]"
    )
    return Panel(table, title=title, border_style="blue")


def build_detail_panel(tasks: list[dict]) -> Panel:
    """Shows full detail of active, failed, or most recent task."""

    # Priority: in_progress > failed > most recent done
    active = next((t for t in tasks if t.get("status") == "in_progress"), None)
    failed = [t for t in tasks if t.get("status") == "failed"]
    recent_done = next((t for t in tasks if t.get("status") == "done"), None)

    lines = []

    # --- IN PROGRESS ---
    if active:
        lines.append(f"[bold cyan]IN PROGRESS[/bold cyan]  [dim]{active.get('id')}[/dim]")
        lines.append(f"[dim]Type:[/dim] {active.get('type')}  |  [dim]Started:[/dim] {active.get('created_at','')[:19].replace('T',' ')}")
        lines.append("")
        lines.append("[bold]Prompt:[/bold]")
        lines.append(f"[white]{truncate(active.get('prompt',''), 400)}[/white]")
        lines.append("")
        lines.append("[dim italic]Worker is processing... waiting for result.[/dim italic]")

    # --- FAILED TASKS ---
    if failed:
        if lines:
            lines.append("")
            lines.append("─" * 60)
            lines.append("")
        lines.append(f"[bold red]FAILED TASKS ({len(failed)})[/bold red]")
        lines.append("")
        for t in failed[:3]:
            lines.append(f"[red]Task:[/red] [bold]{t.get('id')}[/bold]  [{t.get('type')}]")
            lines.append(f"[dim]Prompt:[/dim] {truncate(t.get('prompt',''), 120)}")
            lines.append(f"[red]Error:[/red] {truncate(t.get('error','unknown error'), 200)}")
            completed = t.get("completed_at", "")[:19].replace("T", " ")
            lines.append(f"[dim]Failed at:[/dim] {completed}")
            lines.append("")

    # --- MOST RECENT DONE ---
    if not active and not failed and recent_done:
        lines.append(f"[bold green]LAST COMPLETED[/bold green]  [dim]{recent_done.get('id')}[/dim]")
        lines.append(f"[dim]Type:[/dim] {recent_done.get('type')}  |  [dim]Model:[/dim] {recent_done.get('model_used','?')}")
        lines.append(f"[dim]Completed:[/dim] {recent_done.get('completed_at','')[:19].replace('T',' ')}")
        lines.append("")
        lines.append("[bold]Prompt:[/bold]")
        lines.append(f"[dim]{truncate(recent_done.get('prompt',''), 200)}[/dim]")
        lines.append("")
        lines.append("[bold]Result:[/bold]")
        lines.append(f"[white]{truncate(recent_done.get('result',''), 500)}[/white]")

    if not lines:
        lines.append("[dim]No active tasks. Drop a .json file into tasks/ to start.[/dim]")

    return Panel(
        "\n".join(lines),
        title="[bold]Task Detail[/bold]",
        border_style="blue",
        padding=(1, 2),
    )


def build_axel_log_panel(max_lines: int = 20) -> Panel:
    """Shows the last N lines of AXEL_LOG.md."""
    log_path = LOGS_DIR / "AXEL_LOG.md"
    if not log_path.exists():
        return Panel("[dim]No AXEL_LOG.md yet.[/dim]", title="[bold]Axel Log[/bold]", border_style="magenta")

    raw = log_path.read_text(encoding="utf-8")
    # Filter to only table rows (lines starting with |)
    lines = [l for l in raw.splitlines() if l.strip().startswith("|") and "---" not in l and "Time" not in l]
    recent = lines[-max_lines:]

    content_lines = []
    for line in recent:
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) >= 3:
            time_str, action, detail = parts[0], parts[1], " | ".join(parts[2:])
            action_style = {
                "TASK COMPLETE": "green",
                "TASK QUEUED": "cyan",
                "DEPLOYED": "bold green",
                "WORKER FLAG": "yellow",
                "ESCALATE": "bold red",
                "SYSTEM UPDATE": "bold blue",
                "SCHEDULE": "dim",
                "ALL CLEAR": "dim green",
            }.get(action, "white")
            content_lines.append(f"[dim]{time_str}[/dim] [{action_style}]{action}[/{action_style}] [dim]{truncate(detail, 55)}[/dim]")

    if not content_lines:
        content_lines.append("[dim]No log entries yet.[/dim]")

    return Panel(
        "\n".join(content_lines),
        title=f"[bold magenta]Axel Log[/bold magenta] [dim](last {max_lines})[/dim]",
        border_style="magenta",
        padding=(0, 1),
    )


def build_footer() -> Panel:
    return Panel(
        "[dim]Ctrl+C to exit  |  logs/<task-id>_report.md  |  --log for full Axel log  |  Refresh: 3s[/dim]",
        box=box.HORIZONTALS,
    )


def build_dashboard() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="top", ratio=2),
        Layout(name="bottom", ratio=3),
        Layout(name="footer", size=3),
    )
    layout["top"].update(build_task_table(load_tasks()))
    layout["bottom"].split_row(
        Layout(name="detail", ratio=3),
        Layout(name="axel_log", ratio=2),
    )

    tasks = load_tasks()
    layout["header"].update(build_header())
    layout["top"].update(build_task_table(tasks))
    layout["bottom"]["detail"].update(build_detail_panel(tasks))
    layout["bottom"]["axel_log"].update(build_axel_log_panel())
    layout["footer"].update(build_footer())

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


def run_log():
    """Full scrollable AXEL_LOG view using Rich pager."""
    log_path = LOGS_DIR / "AXEL_LOG.md"
    if not log_path.exists():
        console.print("[red]AXEL_LOG.md not found.[/red]")
        return
    with console.pager():
        console.print(log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WESA Agent Ecosystem Monitor")
    parser.add_argument("--once", action="store_true", help="Print once and exit")
    parser.add_argument("--log", action="store_true", help="Full scrollable Axel log view")
    args = parser.parse_args()

    if args.log:
        run_log()
    elif args.once:
        run_once()
    else:
        run_live()
