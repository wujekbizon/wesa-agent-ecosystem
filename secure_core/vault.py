"""
secure_core.vault — secrets storage, injection, and redaction.

Design (security spec §4.5):
  - Secrets live OUTSIDE the workspace tree, encrypted at rest.
    Default location: %LOCALAPPDATA%\\wesa\\vault\\secrets.enc (Windows)
                       ~/.wesa/vault/secrets.enc            (other platforms)
  - Encryption backend:
        Windows -> DPAPI (CryptProtectData/CryptUnprotectData via ctypes,
                          no extra dependency, machine+user bound)
        other   -> Fernet with a local keyfile (vault dir, 0600), used for
                          development/sandbox only
  - Workers NEVER call this module. Only Kernel-side adapters (providers)
    call vault.get(name) at call time, in memory, and never log the value.
  - redact() scrubs known secret values + generic credential patterns from
    any string before it is written to logs, audit records, or artifacts.

Each secret entry carries metadata: added, rotate_by, owner — surfaced at
session start the same way the AXEL_ORCHESTRATION hard_deadlines are.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class VaultError(Exception):
    """Raised on vault read/write/decrypt failure."""


# ----------------------------------------------------------------------
# Backends
# ----------------------------------------------------------------------


class _Backend:
    def encrypt(self, plaintext: bytes) -> bytes:
        raise NotImplementedError

    def decrypt(self, ciphertext: bytes) -> bytes:
        raise NotImplementedError


class _DPAPIBackend(_Backend):
    """Windows DPAPI via ctypes — no pywin32 dependency.

    Encrypted blobs are bound to the current user account on the current
    machine. This matches the spec's "machine+user bound, no new dependency"
    requirement.
    """

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def __init__(self):
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def _to_blob(self, data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        blob = self._DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return blob, buf  # keep buf alive

    def _from_blob(self, blob) -> bytes:
        cb = blob.cbData
        pb = blob.pbData
        data = ctypes.string_at(pb, cb)
        self._kernel32.LocalFree(pb)
        return data

    def encrypt(self, plaintext: bytes) -> bytes:
        in_blob, _buf = self._to_blob(plaintext)
        out_blob = self._DATA_BLOB()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(in_blob), "wesa-vault", None, None, None, 0, ctypes.byref(out_blob)
        )
        if not ok:
            raise VaultError("DPAPI CryptProtectData failed")
        return self._from_blob(out_blob)

    def decrypt(self, ciphertext: bytes) -> bytes:
        in_blob, _buf = self._to_blob(ciphertext)
        out_blob = self._DATA_BLOB()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
        if not ok:
            raise VaultError("DPAPI CryptUnprotectData failed (wrong user/machine, or corrupt)")
        return self._from_blob(out_blob)


class _FernetFileBackend(_Backend):
    """Cross-platform fallback: Fernet symmetric encryption with a local
    keyfile (0600). Used on non-Windows (sandbox/dev) and as the documented
    fallback if DPAPI is unavailable.

    NOT recommended for production on a shared machine — DPAPI is preferred
    on Windows. Tracked as open decision #2 in the security spec (Greg may
    choose a password-manager-backed flow instead).
    """

    def __init__(self, key_path: Path):
        from cryptography.fernet import Fernet  # local import: optional dep

        self._Fernet = Fernet
        self.key_path = key_path
        if not key_path.exists():
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(Fernet.generate_key())
            os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        self._fernet = Fernet(key_path.read_bytes())

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        try:
            return self._fernet.decrypt(ciphertext)
        except Exception as e:
            raise VaultError(f"Fernet decrypt failed: {e}")


def _default_vault_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(base) / "wesa" / "vault"
    return Path.home() / ".wesa" / "vault"


def _select_backend(vault_dir: Path) -> _Backend:
    override = os.environ.get("WESA_VAULT_BACKEND", "").lower()
    if override == "fernet":
        return _FernetFileBackend(vault_dir / "vault.key")
    if override == "dpapi":
        return _DPAPIBackend()
    if platform.system() == "Windows":
        try:
            return _DPAPIBackend()
        except (AttributeError, OSError):
            pass
    return _FernetFileBackend(vault_dir / "vault.key")


# ----------------------------------------------------------------------
# Vault
# ----------------------------------------------------------------------


class Vault:
    """Encrypted secret store.

    secrets.enc layout (plaintext JSON before encryption):
        {
          "GOOGLE_API_KEY": {"value": "...", "added": "...", "rotate_by": null,
                              "owner": "axel", "status": "dead"},
          "GITHUB_TOKEN":   {"value": "...", "added": "...", "rotate_by": "2026-12-01",
                              "owner": "greg", "status": "active"},
          ...
        }
    """

    def __init__(self, vault_dir: Path | None = None):
        self.vault_dir = Path(vault_dir) if vault_dir else _default_vault_dir()
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.secrets_path = self.vault_dir / "secrets.enc"
        self._backend = _select_backend(self.vault_dir)

    # ------------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        if not self.secrets_path.exists():
            return {}
        ciphertext = self.secrets_path.read_bytes()
        if not ciphertext:
            return {}
        plaintext = self._backend.decrypt(ciphertext)
        return json.loads(plaintext.decode("utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        plaintext = json.dumps(data, indent=2).encode("utf-8")
        ciphertext = self._backend.encrypt(plaintext)
        tmp = self.secrets_path.with_suffix(".tmp")
        tmp.write_bytes(ciphertext)
        if platform.system() != "Windows":
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self.secrets_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set(
        self,
        name: str,
        value: str,
        owner: str = "axel",
        rotate_by: str | None = None,
        status: str = "active",
    ) -> None:
        data = self._load()
        data[name] = {
            "value": value,
            "added": datetime.now(timezone.utc).isoformat(),
            "rotate_by": rotate_by,
            "owner": owner,
            "status": status,
        }
        self._save(data)

    def get(self, name: str) -> str:
        """Return the secret value. Raises VaultError if missing."""
        data = self._load()
        entry = data.get(name)
        if entry is None:
            raise VaultError(f"secret '{name}' not found in vault")
        return entry["value"]

    def delete(self, name: str) -> None:
        data = self._load()
        data.pop(name, None)
        self._save(data)

    def list_names(self) -> list[str]:
        """Return secret names WITHOUT values — safe to log/print."""
        return sorted(self._load().keys())

    def status_report(self) -> list[dict[str, Any]]:
        """Return metadata (no values) for every secret — for session-start
        rotation warnings, same pattern as AXEL_ORCHESTRATION hard_deadlines."""
        data = self._load()
        out = []
        for name, entry in data.items():
            out.append(
                {
                    "name": name,
                    "added": entry.get("added"),
                    "rotate_by": entry.get("rotate_by"),
                    "owner": entry.get("owner"),
                    "status": entry.get("status"),
                }
            )
        return out

    def values_for_redaction(self) -> list[str]:
        """Return the actual secret values, for building the redaction set.
        Caller must not log or persist this list."""
        data = self._load()
        return [e["value"] for e in data.values() if e.get("value")]


# ----------------------------------------------------------------------
# Redaction
# ----------------------------------------------------------------------

# Generic credential-shaped patterns, independent of any specific vault entry.
_GENERIC_SECRET_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "GOOGLE_API_KEY"),
    (re.compile(r"github_pat_[0-9A-Za-z_]{20,}"), "GITHUB_TOKEN"),
    (re.compile(r"ghp_[0-9A-Za-z]{30,}"), "GITHUB_TOKEN"),
    (re.compile(r"postgresql://[^\s\"']+:[^\s\"'@]+@[^\s\"']+"), "DATABASE_URL"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "API_KEY"),
    (
        re.compile(
            r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{12,}"
        ),
        "CREDENTIAL",
    ),
]


def redact(text: str, extra_values: list[str] | None = None) -> str:
    """Replace known secret values and generic credential patterns with
    `[REDACTED:<name>]`. Safe to call on artifacts, log lines, audit fields.

    `extra_values`: literal secret values to scrub (e.g. from
    Vault.values_for_redaction()) — matched as exact substrings first,
    since they may not fit a generic pattern (DB passwords, etc.).
    """
    if not text:
        return text

    out = text
    for value in extra_values or []:
        if value and len(value) >= 6 and value in out:
            out = out.replace(value, "[REDACTED:VAULT_SECRET]")

    for pattern, label in _GENERIC_SECRET_PATTERNS:
        out = pattern.sub(f"[REDACTED:{label}]", out)

    return out
