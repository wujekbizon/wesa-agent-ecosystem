"""
src.logging_setup — structured logging foundation for the WESA agent ecosystem.

E0.6 (repo hygiene sprint, 2026-06-13): provides a single `configure_logging()`
entry point that daemon.py / web_monitor.py / pipeline_controller.py call once
at startup. Emits JSON Lines to stdout (and optionally a file) so logs can be
grepped/parsed by tooling, while a separate human-readable console handler keeps
the terminal usable for Greg.

Scope note: this module is additive only. It does NOT replace the existing
print()-based AXEL_BRIEFING/AXEL_LOG/AXEL_INBOX rendering pipeline — those stay
as-is (CLAUDE.md "Notification Loop"). This is a parallel structured stream for
debugging and future log aggregation. Migrating individual print() call sites
in daemon.py/web_monitor.py to logger.info(...) with task_id/run_id/agent/
provider fields is tracked as follow-up work (not done in E0 — see session
notes 2026-06-13).

Usage:
    from src.logging_setup import configure_logging
    configure_logging()  # call once, at process start

    import logging
    logger = logging.getLogger(__name__)
    logger.info("task_dispatched", extra={"task_id": "abc123", "agent": "D1"})
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Standard LogRecord attributes — anything else set via `extra=` is treated
# as structured context and merged into the JSON output.
_RESERVED = set(logging.LogRecord(
    name="", level=0, pathname="", lineno=0, msg="", args=None, exc_info=None
).__dict__.keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Renders each LogRecord as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                except TypeError:
                    value = str(value)
                payload[key] = value

        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    level: int = logging.INFO,
    json_file: str | Path | None = "logs/structured.jsonl",
    console: bool = True,
) -> None:
    """Configure the root logger once at process startup.

    - `json_file`: path (relative to cwd, typically agent-ecosystem/) for
      JSON-lines structured output. Pass None to disable. Directory is
      created if missing.
    - `console`: if True, also attach a plain human-readable handler to
      stderr at the same level (does not interfere with existing print()
      output going to stdout).

    Safe to call multiple times — clears any handlers this function
    previously added (idempotent for test suites / reloads).
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove handlers we previously installed (tagged via attribute below).
    for h in list(root.handlers):
        if getattr(h, "_wesa_structured", False):
            root.removeHandler(h)

    if json_file:
        path = Path(json_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler._wesa_structured = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        console_handler._wesa_structured = True  # type: ignore[attr-defined]
        root.addHandler(console_handler)
