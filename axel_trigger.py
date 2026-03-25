"""
axel_trigger.py — Axel Headless Session Trigger

Launches a Claude Code CLI session non-interactively to trigger Axel's
orchestration loop without Greg having to open Cowork manually.

Usage:
    python axel_trigger.py
    python axel_trigger.py "Axel, check inbox and proceed autonomously"

Trigger via ntfy:
    Send "axel:check inbox" to your ntfy topic.
    The daemon creates an 'axel' task, which runs this script.

Requirements:
    - Claude Code CLI installed: claude --version
    - CLAUDE.md present in WesaTeam/ (parent of agent-ecosystem/)
    - NTFY_TOPIC env var set (optional, for bookend notifications)
"""

import os
import sys
import shutil
import subprocess
import logging
from pathlib import Path
from datetime import datetime

# --- Paths ---
SCRIPT_DIR  = Path(__file__).resolve().parent          # agent-ecosystem/
WESA_DIR    = SCRIPT_DIR.parent                        # WesaTeam/ — where CLAUDE.md lives
LOGS_DIR    = SCRIPT_DIR / "logs"
LOG_FILE    = LOGS_DIR / "axel_trigger.log"
CLAUDE_MD   = WESA_DIR / "CLAUDE.md"

# --- Logging ---
LOGS_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [axel-trigger] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# --- Locate claude CLI ---
def _find_claude() -> str | None:
    """
    Resolve the full path to the claude CLI.

    The daemon may inherit a restricted PATH (system PATH only, no npm global bin).
    Resolution order:
      1. CLAUDE_BIN env var (explicit override, set in .env)
      2. shutil.which (respects current process PATH)
      3. Common Windows install locations (npm global, standalone installer)
    """
    # 1. Explicit override
    env_override = os.getenv("CLAUDE_BIN", "").strip()
    if env_override and Path(env_override).exists():
        return env_override

    # 2. Which
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    candidates = [
        # Claude Code standalone installer (Windows)
        home / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
        home / "AppData" / "Local" / "AnthropicClaude" / "claude.exe",
        # npm global install (Windows)
        home / "AppData" / "Roaming" / "npm" / "claude.cmd",
        home / "AppData" / "Roaming" / "npm" / "claude",
        # nvm / volta / fnm managed node
        home / ".volta" / "bin" / "claude",
        home / ".nvm" / "bin" / "claude",
        # Unix fallbacks (for testing in sandbox)
        Path("/usr/local/bin/claude"),
        Path("/usr/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


CLAUDE_BIN = _find_claude()

# --- Default message ---
DEFAULT_MESSAGE = "Axel, check inbox and proceed autonomously"


def _send_ntfy(title: str, message: str) -> None:
    """Push a notification to ntfy if NTFY_TOPIC is set."""
    import urllib.request
    topic = os.getenv("NTFY_TOPIC", "")
    if not topic:
        return
    try:
        url = f"https://ntfy.sh/{topic}"
        # Prefix body with [axel-trigger] so the daemon's echo filter can drop it
        body = f"[axel-trigger] {message}"
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
        req.add_header("Title", title)
        req.add_header("Priority", "default")
        req.add_header("Content-Type", "text/plain")
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.warning(f"ntfy push failed: {e}")


def run(message: str = DEFAULT_MESSAGE) -> int:
    """
    Launch a headless Claude Code session with the given message.

    Returns the subprocess exit code (0 = success).
    """
    # Sanity checks
    if not CLAUDE_MD.exists():
        logger.error(f"CLAUDE.md not found at {CLAUDE_MD}. Axel cannot load context.")
        _send_ntfy("Axel Trigger FAILED", f"CLAUDE.md missing at {WESA_DIR}")
        return 1

    if not CLAUDE_BIN:
        logger.error("Claude Code CLI not found. Checked PATH and common Windows install locations.")
        logger.error("Tried: AppData/Local/Programs/claude, AppData/Roaming/npm/claude, volta, nvm")
        logger.error("Fix: run 'where claude' in PowerShell and set CLAUDE_BIN env var, or add its folder to system PATH.")
        _send_ntfy("Axel Trigger FAILED", "claude CLI not found — check CLAUDE_BIN or system PATH")
        return 1

    logger.info(f"Found claude at: {CLAUDE_BIN}")

    try:
        subprocess.run([CLAUDE_BIN, "--version"], capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Claude Code CLI found but version check failed: {e}")
        _send_ntfy("Axel Trigger FAILED", f"claude --version failed: {e}")
        return 1

    logger.info(f"Starting Axel session. cwd={WESA_DIR}")
    logger.info(f"Message: {message}")
    _send_ntfy("Axel Session Started", message[:100])

    started_at = datetime.now()
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--print", message],
            cwd=str(WESA_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,  # 10 minute hard cap
        )
    except subprocess.TimeoutExpired:
        logger.error("Axel session timed out after 600 seconds.")
        _send_ntfy("Axel Session TIMEOUT", "Session killed after 10 minutes.")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error running claude: {e}")
        _send_ntfy("Axel Session ERROR", str(e)[:200])
        return 1

    elapsed = (datetime.now() - started_at).seconds
    exit_code = result.returncode

    # Log full output
    if result.stdout:
        logger.info("=== Axel stdout ===")
        for line in result.stdout.splitlines():
            logger.info(line)
    if result.stderr:
        logger.info("=== Axel stderr ===")
        for line in result.stderr.splitlines():
            logger.info(line)

    if exit_code == 0:
        logger.info(f"Axel session complete in {elapsed}s (exit 0).")
        _send_ntfy("Axel Session Done", f"Completed in {elapsed}s.")
    else:
        logger.error(f"Axel session exited with code {exit_code} after {elapsed}s.")
        stderr_tail = (result.stderr or "")[-200:]
        _send_ntfy("Axel Session FAILED", f"Exit {exit_code}: {stderr_tail}")

    return exit_code


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MESSAGE
    sys.exit(run(msg))
