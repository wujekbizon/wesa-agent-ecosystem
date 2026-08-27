"""P0 containment regression tests.

These tests avoid importing MCP runtime dependencies. They verify the deployed
configuration/source boundaries directly. P2 gaps stay explicit xfails until the
greenfield Secure Engine implements signed envelopes and the Validation Gate.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "security"


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    [
        "daemon.py",
        "wesa-mcp-servers/file_server/server.py",
        "wesa-mcp-servers/shell_server/server.py",
        "wesa-mcp-servers/browser_server/server.py",
    ],
)
def test_containment_python_sources_parse(relative: str):
    ast.parse(_text(relative), filename=relative)


def test_mcp_docker_ports_are_loopback_only():
    compose = _text("wesa-mcp-servers/docker-compose.yml")
    for port in (8001, 8002, 8003):
        assert f'"127.0.0.1:{port}:{port}"' in compose
        assert f'- "{port}:{port}"' not in compose


def test_file_server_mutations_default_to_disabled():
    source = _text("wesa-mcp-servers/file_server/server.py")
    assert 'WESA_MCP_ALLOW_MUTATIONS", "false"' in source
    assert source.count("_require_mutations_enabled()") >= 2


def test_shell_execution_defaults_to_disabled():
    source = _text("wesa-mcp-servers/shell_server/server.py")
    assert 'WESA_MCP_ALLOW_EXEC", "false"' in source
    assert "_require_exec_enabled()" in source


def test_browser_active_operations_default_to_disabled():
    source = _text("wesa-mcp-servers/browser_server/server.py")
    assert 'WESA_MCP_ALLOW_INTERACTION", "false"' in source
    assert 'WESA_MCP_ALLOW_SCREENSHOT_WRITE", "false"' in source
    assert source.count("_require_interaction_enabled()") >= 3


def test_git_agent_output_is_not_auto_executed():
    tree = ast.parse(_text("daemon.py"))
    process_task = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "process_task"
    )
    calls = {
        node.func.id
        for node in ast.walk(process_task)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "execute_git_block" not in calls


def test_worker_skills_have_no_shell_server():
    for path in (ROOT / "agents" / "skills").glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert "shell_server" not in (data.get("mcp_servers") or []), path.name


@pytest.mark.xfail(reason="P2: signed TaskEnvelopeV2 is intentionally greenfield work")
def test_unsigned_task_fixture_is_rejected():
    data = json.loads((FIXTURES / "unsigned_task.json").read_text(encoding="utf-8"))
    assert {"issuer", "issued_at", "nonce", "sig"}.issubset(data)


@pytest.mark.xfail(reason="P2: Validation Gate path canonicalization is intentionally greenfield work")
def test_traversal_task_fixture_is_rejected():
    data = json.loads((FIXTURES / "traversal_task.json").read_text(encoding="utf-8"))
    assert ".." not in Path(data["output_file"]).parts
