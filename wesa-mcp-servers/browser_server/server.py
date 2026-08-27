import base64
import ipaddress
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import uvicorn
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright
from starlette.responses import JSONResponse
from starlette.routing import Route

# Module-level globals for persistent async browser session
_playwright = None
_browser = None
_context = None
_page = None

MCP_BIND_HOST = os.getenv("WESA_MCP_BIND_HOST", "127.0.0.1")
INTERACTION_ENABLED = os.getenv("WESA_MCP_ALLOW_INTERACTION", "false").lower() == "true"
SCREENSHOT_WRITE_ENABLED = (
    os.getenv("WESA_MCP_ALLOW_SCREENSHOT_WRITE", "false").lower() == "true"
)

mcp = FastMCP("wesa-browser-server")

_mcp_app = mcp.streamable_http_app()
_existing_lifespan = _mcp_app.router.lifespan_context


@asynccontextmanager
async def _lifespan(app):
    global _playwright, _browser, _context, _page
    async with _existing_lifespan(app):
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
        _context = await _browser.new_context()
        _page = await _context.new_page()
        yield
        if _context:
            await _context.close()
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()


_mcp_app.router.lifespan_context = _lifespan
_mcp_app.routes.insert(0, Route("/health", lambda req: JSONResponse({"status": "ok"})))
app = _mcp_app


def _require_interaction_enabled() -> None:
    if not INTERACTION_ENABLED:
        raise PermissionError("Browser interaction disabled by P0 containment")


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("Local network URLs are blocked")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("Non-public IP addresses are blocked")


@mcp.tool()
async def navigate(url: str) -> str:
    """Navigate the browser to the specified URL."""
    if _page is None:
        raise RuntimeError("Browser not initialised")
    _validate_public_url(url)
    await _page.goto(url)
    return f"Navigated to {url}"


@mcp.tool()
async def get_text() -> str:
    """Get visible text content of the current page."""
    if _page is None:
        raise RuntimeError("Browser not initialised")
    try:
        return await _page.inner_text("body")
    except Exception:
        return ""


@mcp.tool()
async def get_html() -> str:
    """Get full HTML source of the current page."""
    if _page is None:
        raise RuntimeError("Browser not initialised")
    return await _page.content()


@mcp.tool()
async def click(selector: str) -> str:
    """Click element matching CSS selector."""
    _require_interaction_enabled()
    if _page is None:
        raise RuntimeError("Browser not initialised")
    try:
        await _page.click(selector)
    except Exception as e:
        raise RuntimeError(f"Selector not found: {selector}") from e
    return f"Clicked {selector}"


@mcp.tool()
async def fill(selector: str, value: str) -> str:
    """Fill input field matching CSS selector with value."""
    _require_interaction_enabled()
    if _page is None:
        raise RuntimeError("Browser not initialised")
    try:
        await _page.fill(selector, value)
    except Exception as e:
        raise RuntimeError(f"Selector not found: {selector}") from e
    return f"Filled {selector}"


@mcp.tool()
async def screenshot(path: str = "") -> str:
    """Take screenshot — saves to path if given, else returns base64 PNG."""
    if _page is None:
        raise RuntimeError("Browser not initialised")
    data = await _page.screenshot(type="png")
    if path:
        if not SCREENSHOT_WRITE_ENABLED:
            raise PermissionError("Screenshot file writes disabled by P0 containment")
        with open(path, "wb") as f:
            f.write(data)
        return f"Screenshot saved to {path}"
    return base64.b64encode(data).decode("utf-8")


@mcp.tool()
async def evaluate(script: str) -> str:
    """Execute JavaScript in the page context and return the result."""
    _require_interaction_enabled()
    if _page is None:
        raise RuntimeError("Browser not initialised")
    try:
        result = await _page.evaluate(script)
        return str(result)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def reset_browser() -> str:
    """Close the current browser context and open a fresh one."""
    _require_interaction_enabled()
    global _context, _page
    if _context is not None:
        await _context.close()
    _context = await _browser.new_context()
    _page = await _context.new_page()
    return "Browser reset"


if __name__ == "__main__":
    uvicorn.run(app, host=MCP_BIND_HOST, port=8003)
