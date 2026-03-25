from concurrent.futures import thread
import os
import logging
import urllib.request
import json
import threading

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
        # Prefix body so the inbound listener can filter own echoes with a single check
        data = f"[daemon] {message}".encode("utf-8")
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

def _listen_ntfy(topic: str, callback, stop_event: threading.Event) -> None:
    url = f"https://ntfy.sh/{topic}/json"
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/x-ndjson")
        with urllib.request.urlopen(req, timeout=None) as response:
            for raw_line in response:
                if stop_event.is_set():
                    break
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # ntfy sends keepalive "open" events too, filter to real messages
                if msg.get("event") == "message":
                    callback(msg)
    except Exception as e:
        logger.warning(f"ntfy listener error: {e}")


def start_ntfy_listener(callback, stop_event: threading.Event) -> threading.Thread | None:
    topic = os.getenv("NTFY_TOPIC", "")
    if not topic:
        logger.warning("NTFY_TOPIC not set, listener not started")
        return None
    thread = threading.Thread(
        target=_listen_ntfy,
        args=(topic, callback, stop_event),
        daemon=True,
        name="ntfy-listener",
    )
    thread.start()
    logger.info(f"ntfy listener started on topic '{topic}'")
    return thread