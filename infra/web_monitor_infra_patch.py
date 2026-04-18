"""
Lumio Infra Tab — web_monitor.py patch
Add these routes to your existing web_monitor.py.
They proxy to infra_manager running on the Ubuntu VM.

Usage: merge this into web_monitor.py manually or via D1 task.
"""

import os
import logging
import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

log = logging.getLogger("monitor.infra")

# Set LUMIO_INFRA_MANAGER_URL in your .env on the Windows PC
INFRA_MANAGER_URL = os.environ.get("LUMIO_INFRA_MANAGER_URL", "http://192.168.1.50:8766")


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------
async def _infra_get(path: str) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{INFRA_MANAGER_URL}{path}")
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Infra manager unreachable. Is Ubuntu VM running?")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _infra_post(path: str, body: dict = None) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{INFRA_MANAGER_URL}{path}", json=body or {})
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Infra manager unreachable. Is Ubuntu VM running?")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Routes to add to web_monitor.py
# ---------------------------------------------------------------------------
# @app.get("/api/infra/services")
async def infra_list_services():
    return await _infra_get("/services")


# @app.get("/api/infra/services/{service_name}")
async def infra_get_service(service_name: str):
    return await _infra_get(f"/services/{service_name}")


# @app.post("/api/infra/services/{service_name}/start")
async def infra_start_service(service_name: str):
    return await _infra_post(f"/services/{service_name}/start")


# @app.post("/api/infra/services/{service_name}/stop")
async def infra_stop_service(service_name: str):
    return await _infra_post(f"/services/{service_name}/stop")


# @app.post("/api/infra/services/{service_name}/restart")
async def infra_restart_service(service_name: str):
    return await _infra_post(f"/services/{service_name}/restart")


# @app.post("/api/infra/services/start-all")
async def infra_start_all():
    return await _infra_post("/services/start-all")


# @app.post("/api/infra/services/stop-all")
async def infra_stop_all():
    return await _infra_post("/services/stop-all")


# @app.get("/api/infra/services/{service_name}/logs")
async def infra_get_logs(service_name: str, tail: int = 100):
    return await _infra_get(f"/services/{service_name}/logs?tail={tail}")


# @app.get("/api/infra/health")
async def infra_health():
    return await _infra_get("/health")
