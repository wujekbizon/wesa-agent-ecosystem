"""
WESA Agent Ecosystem - Notifier
Local Windows desktop notifications for task events.
Built to be swappable - replace _send() later with ntfy/Gotify for K3s homelab.
"""

import subprocess
import sys
from datetime import datetime


def _send_windows(title: str, message: str):
    """Windows 10/11 toast notification via PowerShell. No extra deps."""
    script = (
        f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
        f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        f"[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        f"$template.GetElementsByTagName('text')[0].AppendChild($template.CreateTextNode('{title}')) | Out-Null;"
        f"$template.GetElementsByTagName('text')[1].AppendChild($template.CreateTextNode('{message}')) | Out-Null;"
        f"$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('WESA Agent Daemon');"
        f"$notifier.Show([Windows.UI.Notifications.ToastNotification]::new($template));"
    )
    subprocess.run(
        ["powershell", "-Command", script],
        capture_output=True,
        timeout=5,
    )


def _send_fallback(title: str, message: str):
    """Fallback: print to terminal if notifications fail."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{timestamp}] NOTIFICATION: {title} - {message}\n")


def notify(title: str, message: str):
    """
    Send a notification. Tries Windows toast first, falls back to terminal print.
    To swap to ntfy later: replace this function body with an HTTP POST to your ntfy server.
    """
    try:
        if sys.platform == "win32":
            _send_windows(title, message)
        else:
            _send_fallback(title, message)
    except Exception:
        _send_fallback(title, message)


# Convenience helpers
def notify_done(task_id: str, task_type: str, model: str):
    notify(
        title=f"Task {task_id} done",
        message=f"[{task_type}] completed via {model}",
    )


def notify_failed(task_id: str, error: str):
    notify(
        title=f"Task {task_id} FAILED",
        message=error[:100],
    )


def notify_started():
    notify(
        title="WESA Daemon started",
        message="Agent ecosystem is online and watching for tasks.",
    )
