"""
secure_core.audit — hash-chained, append-only audit log.

Every record references the hash of the previous record (`prev_hash`).
Any deletion, edit, or reordering of past lines breaks the chain, which
`AuditLog.verify()` detects in O(n).

File format: JSON Lines (one record per line) at
    agent-ecosystem/logs/AUDIT.jsonl

This module is part of Zone 1 (Kernel). Workers never import this directly —
the Kernel writes audit records on their behalf.

Spec ref: WesaTeam/_plans/2026-06-09_wesa_security-architecture.md §4.8
Acceptance criterion #6: deleting any line from AUDIT.jsonl makes verify() fail.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


class AuditError(Exception):
    """Raised on audit log corruption or verification failure."""


def _canonical(record: dict[str, Any]) -> str:
    """Deterministic JSON encoding used for hashing (sorted keys, no whitespace)."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(record_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(record_without_hash).encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only, hash-chained audit log.

    Usage:
        audit = AuditLog(Path("agent-ecosystem/logs/AUDIT.jsonl"))
        audit.append(actor="kernel", event="artifact_written", task="...", path="...")
        ok, err = audit.verify()
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    def append(self, actor: str, event: str, **fields: Any) -> dict[str, Any]:
        """Append a new record to the chain. Returns the written record.

        `fields` is free-form context (task id, path, gate report, etc.).
        Any value that looks like a secret should already be redacted by
        the caller — see secure_core.vault.redact().
        """
        with self._lock:
            prev_hash, prev_seq = self._tail_hash_and_seq()
            record = {
                "seq": prev_seq + 1,
                "ts": datetime.now(timezone.utc).isoformat(),
                "actor": actor,
                "event": event,
                **fields,
                "prev_hash": prev_hash,
            }
            record["hash"] = _record_hash(record)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(_canonical(record) + "\n")
            return record

    # ------------------------------------------------------------------
    # Read / verify path
    # ------------------------------------------------------------------
    def _iter_records(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    raise AuditError(f"AUDIT.jsonl line {lineno}: invalid JSON ({e})")

    def _tail_hash_and_seq(self) -> tuple[str, int]:
        """Return (hash, seq) of the last record, or genesis if empty.

        Does not verify the whole chain — callers should run verify()
        at session start; this is just for computing the next link.
        """
        last_hash = GENESIS_HASH
        last_seq = 0
        for record in self._iter_records():
            last_hash = record.get("hash", GENESIS_HASH)
            last_seq = record.get("seq", last_seq)
        return last_hash, last_seq

    def verify(self) -> tuple[bool, str | None]:
        """Verify the entire chain.

        Returns (True, None) if the chain is intact, or
        (False, "<reason>") on the first inconsistency found.
        """
        expected_prev = GENESIS_HASH
        expected_seq = 0
        try:
            for record in self._iter_records():
                expected_seq += 1
                record_hash = record.get("hash")
                prev_hash = record.get("prev_hash")
                seq = record.get("seq")

                if prev_hash != expected_prev:
                    return False, (
                        f"seq {seq}: prev_hash mismatch "
                        f"(expected {expected_prev}, got {prev_hash}) — "
                        f"chain broken, a record was edited or removed"
                    )
                if seq != expected_seq:
                    return False, (
                        f"seq field {seq} != expected {expected_seq} — "
                        f"a record was reordered or removed"
                    )

                recomputed = {k: v for k, v in record.items() if k != "hash"}
                if _record_hash(recomputed) != record_hash:
                    return False, f"seq {seq}: hash mismatch — record contents were tampered with"

                expected_prev = record_hash
        except AuditError as e:
            return False, str(e)

        return True, None

    def head(self) -> str:
        """Return the current chain head hash (for off-machine checkpointing)."""
        h, _ = self._tail_hash_and_seq()
        return h

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        """Return the last n records (for session-start summaries)."""
        records = list(self._iter_records())
        return records[-n:]


def verify_at_session_start(path: Path) -> str:
    """Convenience for the WAL recovery checklist.

    Returns a one-line human-readable status string. Never raises —
    a missing file is treated as "empty, OK" (first run).
    """
    if not Path(path).exists():
        return "AUDIT.jsonl: not found (first run — OK)"
    audit = AuditLog(path)
    ok, err = audit.verify()
    if ok:
        records = list(audit._iter_records())
        return f"AUDIT.jsonl: OK ({len(records)} records, head={audit.head()[:12]}...)"
    return f"AUDIT.jsonl: TAMPER DETECTED — {err}"
