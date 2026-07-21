"""Tests for secure_core.audit — hash-chained append-only log."""

import json

from secure_core.audit import AuditLog, verify_at_session_start


def test_append_and_verify_empty_log(tmp_path):
    audit = AuditLog(tmp_path / "AUDIT.jsonl")
    ok, err = audit.verify()
    assert ok is True
    assert err is None


def test_append_chains_records(tmp_path):
    audit = AuditLog(tmp_path / "AUDIT.jsonl")
    r1 = audit.append(actor="axel", event="task_queued", task_id="t1")
    r2 = audit.append(actor="axel", event="task_done", task_id="t1")

    assert r2["prev_hash"] == r1["hash"]
    assert r1["seq"] == 1
    assert r2["seq"] == 2

    ok, err = audit.verify()
    assert ok is True
    assert err is None


def test_tampering_breaks_verification(tmp_path):
    path = tmp_path / "AUDIT.jsonl"
    audit = AuditLog(path)
    audit.append(actor="axel", event="task_queued", task_id="t1")
    audit.append(actor="axel", event="task_done", task_id="t1")

    # Tamper: edit a field in the first record
    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["task_id"] = "tampered"
    lines[0] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, err = audit.verify()
    assert ok is False
    assert err is not None


def test_deleting_a_line_breaks_verification(tmp_path):
    path = tmp_path / "AUDIT.jsonl"
    audit = AuditLog(path)
    audit.append(actor="axel", event="a")
    audit.append(actor="axel", event="b")
    audit.append(actor="axel", event="c")

    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]  # remove the middle record
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, err = audit.verify()
    assert ok is False


def test_verify_at_session_start_missing_file(tmp_path):
    msg = verify_at_session_start(tmp_path / "does_not_exist.jsonl")
    assert "not found" in msg


def test_verify_at_session_start_ok(tmp_path):
    path = tmp_path / "AUDIT.jsonl"
    audit = AuditLog(path)
    audit.append(actor="axel", event="a")

    msg = verify_at_session_start(path)
    assert "OK" in msg
