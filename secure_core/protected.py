"""
secure_core.protected — Protected File Registry.

Loads protected.yaml and answers "is this path protected?" / "does this
change require Greg's approval?". Used by the Validation Gate (Stage 1,
not yet built — S1) and available now for any write path that wants to
self-check before touching disk.

Spec ref: WesaTeam/_plans/2026-06-09_wesa_security-architecture.md §4.6
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path

import yaml


class ProtectedFileRegistry:
    def __init__(self, registry_path: Path, agent_root: Path, wesa_root: Path | None = None):
        """
        Args:
            registry_path: path to protected.yaml
            agent_root: absolute path to agent-ecosystem/ (patterns under
                        agent_root_protected are relative to this)
            wesa_root: absolute path to WesaTeam/ (patterns under
                       wesa_root_protected are relative to this). Defaults
                       to agent_root.parent.
        """
        self.registry_path = Path(registry_path)
        self.agent_root = Path(agent_root).resolve()
        self.wesa_root = Path(wesa_root).resolve() if wesa_root else self.agent_root.parent

        with open(self.registry_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self._agent_patterns: list[str] = data.get("agent_root_protected", [])
        self._wesa_patterns: list[str] = data.get("wesa_root_protected", [])
        self._greg_patterns: list[str] = data.get("greg_approval_required", [])

    # ------------------------------------------------------------------
    @staticmethod
    def _matches(rel_posix: str, patterns: Iterable[str]) -> bool:
        for pattern in patterns:
            if fnmatch.fnmatch(rel_posix, pattern):
                return True
            # ** at end should also match the bare directory itself
            if pattern.endswith("/**") and fnmatch.fnmatch(rel_posix, pattern[:-3]):
                return True
        return False

    def _relativize(self, path: Path) -> tuple[str, list[str]] | None:
        """Return (relative_posix_path, applicable_patterns) for whichever
        root the path falls under, or None if outside both roots."""
        path = Path(path).resolve()
        try:
            rel = path.relative_to(self.agent_root)
            return rel.as_posix(), self._agent_patterns
        except ValueError:
            pass
        try:
            rel = path.relative_to(self.wesa_root)
            return rel.as_posix(), self._wesa_patterns
        except ValueError:
            pass
        return None

    # ------------------------------------------------------------------
    def is_protected(self, path: Path) -> bool:
        """True if `path` (absolute, or resolved relative to agent_root)
        matches any protected pattern. Paths outside both roots entirely
        are treated as protected by default-deny (a worker has no business
        writing there)."""
        relativized = self._relativize(path)
        if relativized is None:
            return True  # default deny: outside known roots
        rel, patterns = relativized
        return self._matches(rel, patterns)

    def requires_greg_approval(self, path: Path) -> bool:
        relativized = self._relativize(path)
        if relativized is None:
            return True
        rel, _ = relativized
        return self._matches(rel, self._greg_patterns)
