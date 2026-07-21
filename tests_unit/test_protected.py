"""Tests for secure_core.protected — Protected File Registry."""

from pathlib import Path

import pytest

from secure_core.protected import ProtectedFileRegistry

AGENT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = AGENT_ROOT / "secure_core" / "protected.yaml"


@pytest.fixture
def registry():
    return ProtectedFileRegistry(
        registry_path=REGISTRY_PATH,
        agent_root=AGENT_ROOT,
        wesa_root=AGENT_ROOT.parent,
    )


def test_secure_core_is_protected(registry):
    assert registry.is_protected(AGENT_ROOT / "secure_core" / "vault.py")


def test_env_is_protected(registry):
    assert registry.is_protected(AGENT_ROOT / ".env")


def test_ordinary_src_file_not_protected(registry):
    assert not registry.is_protected(AGENT_ROOT / "src" / "router.py")


def test_path_outside_known_roots_is_protected_default_deny(registry):
    assert registry.is_protected(Path("/etc/passwd"))


def test_secure_core_requires_greg_approval(registry):
    assert registry.requires_greg_approval(AGENT_ROOT / "secure_core" / "audit.py")


def test_ordinary_src_file_does_not_require_greg_approval(registry):
    assert not registry.requires_greg_approval(AGENT_ROOT / "src" / "router.py")


def test_wesa_plans_are_protected(registry):
    assert registry.is_protected(AGENT_ROOT.parent / "_plans" / "anything.md")
