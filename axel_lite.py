import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure the root directory is accessible for imports
root_path = Path(__file__).resolve().parent
sys.path.insert(0, str(root_path))

import requests  # noqa: E402 -- import after sys.path setup is intentional

# --- CONSTANTS ---
AXEL_MODE_FILE = Path("AXEL_MODE")
BRIEFING_FILE = Path("logs/AXEL_BRIEFING.md")
MASTER_PLAN_FILE = Path("WesaTeam/_plans/2026-03-21_wesa_master-plan.md")
AXEL_LOG_FILE = Path("WesaTeam/agent-ecosystem/logs/AXEL_LOG.md")
TASKS_DIR = Path("tasks")
DB_PATH = "logs/tasks.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:35b"
MAX_TASKS = 5
VALID_TYPES = {
    "code",
    "research",
    "write",
    "design",
    "review",
    "test",
    "skill_update",
    "assets",
    "manage",
    "frontend",
    "coordinate",
}

AXEL_LITE_PROMPT = """\
You are Axel-Lite, a headless fallback orchestrator for the WESA agent ecosystem.
Greg is unavailable. Your job: review the current state and queue the next logical tasks.

Rules:
- Output ONLY a valid JSON array. No explanation, no markdown fences, no extra text.
- Each task object must have: "id", "type", "prompt"
- "id" must be unique, short, lowercase with hyphens (e.g. "axl-2026-03-23-001")
- "type" must be one of: code, research, write, design, review, test, skill_update, assets, manage, frontend, coordinate
- "prompt" must be a clear, self-contained task description (no references to "the previous task")
- Maximum {max_tasks} tasks
- Do not re-queue tasks that are already done or in progress per the briefing
- Focus on what unblocks the master plan most

Current state:
{context}

Output the JSON array now:
"""


def read_safe(path: Path) -> str:
    """Returns file text or empty string if missing."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def check_mode() -> bool:
    """Returns True if AXEL_MODE_FILE exists and content.strip() == 'headless'."""
    if not AXEL_MODE_FILE.exists():
        return False
    content = AXEL_MODE_FILE.read_text(encoding="utf-8").strip()
    return content == "headless"


def build_context() -> str:
    """Reads BRIEFING_FILE and first 150 lines of MASTER_PLAN_FILE."""
    briefing = read_safe(BRIEFING_FILE)

    plan_text = read_safe(MASTER_PLAN_FILE)
    lines = plan_text.splitlines()[:150]
    plan_excerpt = "\n".join(lines)

    return f"=== AXEL BRIEFING ===\n{briefing}\n\n=== MASTER PLAN (excerpt) ===\n{plan_excerpt}"


def call_qwen(prompt: str) -> str:
    """POSTs to OLLAMA_URL and returns response body."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3},
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=180)
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ollama request failed: {e}")
    except json.JSONDecodeError:
        raise RuntimeError("Ollama returned invalid JSON response")


def parse_tasks(raw: str) -> list[dict]:
    """
    Extracts JSON array from raw response.
    Validates type and prompt. Returns max MAX_TASKS valid items.
    """
    raw = raw.strip()

    # Remove markdown fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw = raw.strip()

    # Find the JSON array boundaries
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in response. Raw: {raw[:200]}")

    array_str = raw[start : end + 1]

    try:
        tasks = json.loads(array_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse failed: {e}. Raw array: {array_str[:300]}")

    if not isinstance(tasks, list):
        raise ValueError("Parsed JSON is not a list")

    valid = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task_type = str(item.get("type", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        task_id = str(item.get("id", "")).strip()
        if task_type not in VALID_TYPES:
            continue
        if not prompt or not task_id:
            continue
        valid.append({"id": task_id, "type": task_type, "prompt": prompt})
        if len(valid) >= MAX_TASKS:
            break

    return valid


def write_tasks(tasks: list[dict]) -> list[str]:
    """Writes each task as a JSON file to TASKS_DIR. Returns list of written file paths."""
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for task in tasks:
        task_file = TASKS_DIR / f"{task['id']}.json"
        task_file.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(str(task_file))
    return written


def append_log(message: str) -> None:
    """Appends a timestamped line to AXEL_LOG_FILE."""
    timestamp = datetime.now().strftime("%H:%M")
    line = f"| {timestamp} | AXEL-LITE | {message} |\n"
    try:
        with open(AXEL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass  # never crash on log failure


def run() -> None:
    """Main entry point. Checks mode flag, builds context, calls qwen, drops tasks."""
    if not check_mode():
        print("[axel-lite] AXEL_MODE flag not set to 'headless'. Exiting.")
        return

    print("[axel-lite] Headless mode active. Building context...")
    append_log("Headless mode triggered")

    context = build_context()
    if not context.strip():
        print("[axel-lite] WARNING: context is empty. Briefing and master plan both missing.")
        append_log("WARNING — context empty, briefing and plan missing")

    prompt = AXEL_LITE_PROMPT.format(context=context, max_tasks=MAX_TASKS)

    print(f"[axel-lite] Calling {OLLAMA_MODEL} via Ollama...")
    try:
        raw = call_qwen(prompt)
    except RuntimeError as e:
        print(f"[axel-lite] ERROR: {e}")
        append_log(f"ERROR — Ollama call failed: {e}")
        return

    print("[axel-lite] Parsing response...")
    try:
        tasks = parse_tasks(raw)
    except ValueError as e:
        print(f"[axel-lite] ERROR: {e}")
        append_log(f"ERROR — parse failed: {e}")
        return

    if not tasks:
        print("[axel-lite] No valid tasks parsed. Nothing queued.")
        append_log("No valid tasks parsed from qwen response")
        return

    written = write_tasks(tasks)
    print(f"[axel-lite] Queued {len(written)} task(s):")
    for path in written:
        print(f"  {path}")

    task_ids = ", ".join(t["id"] for t in tasks)
    append_log(f"Queued {len(tasks)} task(s): {task_ids}")

    # Clear the mode flag so daemon doesn't re-trigger on next poll
    try:
        AXEL_MODE_FILE.unlink()
        print("[axel-lite] AXEL_MODE flag cleared.")
    except OSError:
        pass


if __name__ == "__main__":
    run()
