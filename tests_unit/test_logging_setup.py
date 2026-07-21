"""Tests for src.logging_setup — E0.6 structured JSON logging foundation."""

import json
import logging

from src.logging_setup import JsonFormatter, configure_logging


def test_json_formatter_basic_record():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="wesa.daemon",
        level=logging.INFO,
        pathname="daemon.py",
        lineno=10,
        msg="task dispatched",
        args=None,
        exc_info=None,
    )
    out = json.loads(formatter.format(record))
    assert out["level"] == "INFO"
    assert out["logger"] == "wesa.daemon"
    assert out["msg"] == "task dispatched"
    assert "ts" in out


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="wesa.daemon",
        level=logging.INFO,
        pathname="daemon.py",
        lineno=10,
        msg="task dispatched",
        args=None,
        exc_info=None,
    )
    record.task_id = "abc123"
    record.agent = "D1"
    out = json.loads(formatter.format(record))
    assert out["task_id"] == "abc123"
    assert out["agent"] == "D1"


def test_configure_logging_writes_jsonl(tmp_path):
    log_path = tmp_path / "structured.jsonl"
    configure_logging(json_file=log_path, console=False)

    logger = logging.getLogger("wesa.test")
    logger.info("hello", extra={"task_id": "t1"})

    for handler in logging.getLogger().handlers:
        handler.flush()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["msg"] == "hello"
    assert record["task_id"] == "t1"


def test_configure_logging_is_idempotent(tmp_path):
    log_path = tmp_path / "structured.jsonl"
    configure_logging(json_file=log_path, console=False)
    handlers_after_first = [h for h in logging.getLogger().handlers if getattr(h, "_wesa_structured", False)]

    configure_logging(json_file=log_path, console=False)
    handlers_after_second = [h for h in logging.getLogger().handlers if getattr(h, "_wesa_structured", False)]

    assert len(handlers_after_first) == len(handlers_after_second)


def test_configure_logging_no_json_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    configure_logging(json_file=None, console=False)
    handlers = [h for h in logging.getLogger().handlers if getattr(h, "_wesa_structured", False)]
    assert all(not isinstance(h, logging.FileHandler) for h in handlers)
