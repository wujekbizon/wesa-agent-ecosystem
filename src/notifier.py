import os
import logging
import urllib.request

logger = logging.getLogger(__name__)

def _send_windows(title: str, message: str) -> None:
    # Windows-specific implementation (mocked for context)
    pass

def _send_fallback(title: str, message: str) -> None:
    # Fallback implementation (mocked for context)
    pass

def _send_ntfy(title: str, message: str) -> None:
    topic = os.getenv("NTFY_TOPIC", "")
    if not topic:
        return
    try:
        url = f"https://ntfy.sh/{topic}"
        data = message.encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Title", title)
        req.add_header("Priority", "default")
        req.add_header("Content-Type", "text/plain")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # never crash the daemon

def notify(title: str, message: str) -> None:
    try:
        _send_windows(title, message)
    except Exception:
        pass
    
    try:
        _send_fallback(title, message)
    except Exception:
        pass
    
    try:
        _send_ntfy(title, message)
    except Exception:
        pass

def notify_done(task_id: str, task_type: str, model_used: str) -> None:
    notify(title=f"Task Done: {task_id}", message=f"Type: {task_type} | Model: {model_used}")

def notify_failed(task_id: str, error: str) -> None:
    notify(title=f"Task Failed: {task_id}", message=error)

def notify_started() -> None:
    notify(title="WESA Daemon Started", message="Agent daemon v0.5 is running.")

def notify_pipeline(pipeline_name: str, run_id: str, status: str) -> None:
    notify(
        title=f"Pipeline: {pipeline_name}",
        message=f"Run {run_id} — {status}",
    )