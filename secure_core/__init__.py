"""
secure_core — WESA Secure Core (Zone 1: Kernel).

This package is the ONLY code in the WESA agent ecosystem with rights to
write protected files, hold secrets, or append to the audit log.

Modules:
    audit.py      — hash-chained, append-only audit log (AUDIT.jsonl)
    vault.py       — secrets storage (DPAPI on Windows, Fernet+keyfile fallback)
    protected.py   — Protected File Registry (load + match against protected.yaml)

See: WesaTeam/_plans/2026-06-09_wesa_security-architecture.md
"""

from .audit import AuditError, AuditLog
from .protected import ProtectedFileRegistry
from .vault import Vault, VaultError

__all__ = [
    "AuditLog",
    "AuditError",
    "Vault",
    "VaultError",
    "ProtectedFileRegistry",
]
