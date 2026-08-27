import glob
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Route

FILE_SERVER_ROOT = os.getenv("FILE_SERVER_ROOT", "/workspace")
RESOLVED_ROOT = Path(FILE_SERVER_ROOT).resolve()
MCP_BIND_HOST = os.getenv("WESA_MCP_BIND_HOST", "127.0.0.1")
MUTATIONS_ENABLED = os.getenv("WESA_MCP_ALLOW_MUTATIONS", "false").lower() == "true"

_SENSITIVE_EXACT = {
    ".git",
    "appsettings.development.json",
    "credentials.json",
    "secrets.enc",
}
_SENSITIVE_SUFFIXES = (".key", ".pem", ".p12", ".pfx")

mcp = FastMCP("FileServer")


def _is_sensitive(target: Path) -> bool:
    relative = target.relative_to(RESOLVED_ROOT)
    for part in relative.parts:
        lowered = part.lower()
        if lowered in _SENSITIVE_EXACT or lowered.startswith(".env"):
            return True
        if lowered.endswith(_SENSITIVE_SUFFIXES) or "credential" in lowered:
            return True
    return False


def _resolve_and_validate(path: str) -> Path:
    """Resolve inside root and reject credential-bearing paths."""
    target = (RESOLVED_ROOT / Path(path)).resolve()
    try:
        target.relative_to(RESOLVED_ROOT)
    except ValueError:
        raise ValueError(f"Path escape attempt blocked: {path}")
    if _is_sensitive(target):
        raise PermissionError("Sensitive path access blocked")
    return target


def _require_mutations_enabled() -> None:
    if not MUTATIONS_ENABLED:
        raise PermissionError("File mutations disabled by P0 containment")


@mcp.tool()
def read_file(path: str) -> str:
    """Read file contents from path relative to FILE_SERVER_ROOT."""
    target = _resolve_and_validate(path)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Path is a directory, not a file: {path}")
    return target.read_text()


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to file at path relative to FILE_SERVER_ROOT."""
    _require_mutations_enabled()
    target = _resolve_and_validate(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return "ok"


@mcp.tool()
def list_files(path: str = "", pattern: str = "") -> str:
    """List files in directory or glob matches relative to FILE_SERVER_ROOT."""
    dir_path = _resolve_and_validate(path)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path}")

    if pattern:
        full_pattern = str(dir_path / pattern)
        matches = glob.glob(full_pattern, recursive=True)
        rel_paths = []
        for m in matches:
            try:
                rel = Path(m).relative_to(RESOLVED_ROOT)
                if not _is_sensitive(Path(m).resolve()):
                    rel_paths.append(str(rel))
            except ValueError:
                continue
        return "\n".join(sorted(rel_paths))
    else:
        entries = sorted([e.name for e in dir_path.iterdir() if not _is_sensitive(e.resolve())])
        return "\n".join(entries)


@mcp.tool()
def file_exists(path: str) -> bool:
    """Check if file exists at path relative to FILE_SERVER_ROOT."""
    target = _resolve_and_validate(path)
    return target.exists()


@mcp.tool()
def delete_file(path: str) -> str:
    """Delete file at path relative to FILE_SERVER_ROOT."""
    _require_mutations_enabled()
    target = _resolve_and_validate(path)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Path is a directory, not a file: {path}")
    target.unlink()
    return "ok"


def health_check(request):
    return JSONResponse({"status": "ok"})


app = mcp.streamable_http_app()
app.routes.insert(0, Route("/health", health_check))


if __name__ == "__main__":
    uvicorn.run(app, host=MCP_BIND_HOST, port=8001)
