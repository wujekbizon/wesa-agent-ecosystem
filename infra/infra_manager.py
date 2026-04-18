"""
Lumio Infrastructure Manager
FastAPI service that manages Docker containers for the Lumio learning platform.
Runs on Ubuntu VM, exposed to LAN at port 8766.
WESA web monitor talks to this API to spawn/kill/inspect services.
"""

import os
import logging
from datetime import datetime
from typing import Optional

import docker
from docker.errors import DockerException, NotFound, APIError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SERVICES_CONFIG_PATH = os.environ.get("SERVICES_CONFIG", "/app/configs/services.yaml")
COMPOSE_FILE_PATH = os.environ.get("COMPOSE_FILE", "/app/docker-compose.yml")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("infra_manager")

# ---------------------------------------------------------------------------
# Docker client
# ---------------------------------------------------------------------------
try:
    docker_client = docker.from_env()
    docker_client.ping()
    log.info("Docker daemon connected.")
except DockerException as e:
    log.error(f"Cannot connect to Docker daemon: {e}")
    docker_client = None

# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------
def load_services_config() -> dict:
    try:
        with open(SERVICES_CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        log.warning(f"services.yaml not found at {SERVICES_CONFIG_PATH}, using empty registry")
        return {"services": {}}


def get_service_container_name(service_name: str) -> str:
    return f"lumio-{service_name}"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Lumio Infrastructure Manager",
    description="Controls Docker containers for the Lumio home learning platform",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # home LAN only — no public exposure
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    docker_ok = docker_client is not None
    try:
        if docker_ok:
            docker_client.ping()
    except Exception:
        docker_ok = False
    return {
        "status": "ok" if docker_ok else "degraded",
        "docker": docker_ok,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Service status helpers
# ---------------------------------------------------------------------------
def _container_status(container_name: str) -> dict:
    """Return status dict for a named container."""
    if not docker_client:
        return {"name": container_name, "status": "unknown", "error": "docker unavailable"}
    try:
        container = docker_client.containers.get(container_name)
        attrs = container.attrs
        state = attrs.get("State", {})
        return {
            "name": container_name,
            "status": container.status,           # running / exited / paused / ...
            "running": state.get("Running", False),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "image": container.image.tags[0] if container.image.tags else "unknown",
            "ports": attrs.get("NetworkSettings", {}).get("Ports", {}),
        }
    except NotFound:
        return {"name": container_name, "status": "not_found", "running": False}
    except APIError as e:
        return {"name": container_name, "status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Routes — service list
# ---------------------------------------------------------------------------
@app.get("/services")
def list_services():
    """List all known Lumio services with their live Docker status."""
    config = load_services_config()
    services = config.get("services", {})
    result = []
    for name, meta in services.items():
        container_name = get_service_container_name(name)
        status = _container_status(container_name)
        result.append({
            "id": name,
            "display_name": meta.get("display_name", name),
            "description": meta.get("description", ""),
            "port": meta.get("port"),
            "container": container_name,
            **status,
        })
    return {"services": result, "count": len(result)}


@app.get("/services/{service_name}")
def get_service(service_name: str):
    """Get status for a single service."""
    config = load_services_config()
    services = config.get("services", {})
    if service_name not in services:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not in registry")
    meta = services[service_name]
    container_name = get_service_container_name(service_name)
    status = _container_status(container_name)
    return {
        "id": service_name,
        "display_name": meta.get("display_name", service_name),
        "description": meta.get("description", ""),
        "port": meta.get("port"),
        "container": container_name,
        **status,
    }


# ---------------------------------------------------------------------------
# Routes — service control
# ---------------------------------------------------------------------------
@app.post("/services/{service_name}/start")
def start_service(service_name: str):
    """Start a stopped/exited container."""
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker daemon unavailable")
    container_name = get_service_container_name(service_name)
    try:
        container = docker_client.containers.get(container_name)
        if container.status == "running":
            return {"status": "already_running", "container": container_name}
        container.start()
        log.info(f"Started container: {container_name}")
        return {"status": "started", "container": container_name}
    except NotFound:
        raise HTTPException(
            status_code=404,
            detail=f"Container '{container_name}' not found. Run docker-compose up first."
        )
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/services/{service_name}/stop")
def stop_service(service_name: str):
    """Stop a running container (graceful)."""
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker daemon unavailable")
    container_name = get_service_container_name(service_name)
    try:
        container = docker_client.containers.get(container_name)
        if container.status != "running":
            return {"status": "already_stopped", "container": container_name}
        container.stop(timeout=10)
        log.info(f"Stopped container: {container_name}")
        return {"status": "stopped", "container": container_name}
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/services/{service_name}/restart")
def restart_service(service_name: str):
    """Restart a container."""
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker daemon unavailable")
    container_name = get_service_container_name(service_name)
    try:
        container = docker_client.containers.get(container_name)
        container.restart(timeout=10)
        log.info(f"Restarted container: {container_name}")
        return {"status": "restarted", "container": container_name}
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/services/start-all")
def start_all():
    """Start all known services."""
    config = load_services_config()
    results = {}
    for name in config.get("services", {}):
        try:
            container = docker_client.containers.get(get_service_container_name(name))
            if container.status != "running":
                container.start()
                results[name] = "started"
            else:
                results[name] = "already_running"
        except NotFound:
            results[name] = "not_found"
        except Exception as e:
            results[name] = f"error: {e}"
    return {"results": results}


@app.post("/services/stop-all")
def stop_all():
    """Stop all running Lumio services."""
    config = load_services_config()
    results = {}
    for name in config.get("services", {}):
        try:
            container = docker_client.containers.get(get_service_container_name(name))
            if container.status == "running":
                container.stop(timeout=10)
                results[name] = "stopped"
            else:
                results[name] = "already_stopped"
        except NotFound:
            results[name] = "not_found"
        except Exception as e:
            results[name] = f"error: {e}"
    return {"results": results}


# ---------------------------------------------------------------------------
# Routes — logs
# ---------------------------------------------------------------------------
@app.get("/services/{service_name}/logs")
def get_logs(service_name: str, tail: int = 100, stream: bool = False):
    """
    Get container logs.
    tail=N returns last N lines.
    stream=true returns a streaming response (SSE-compatible).
    """
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker daemon unavailable")
    container_name = get_service_container_name(service_name)
    try:
        container = docker_client.containers.get(container_name)
        if stream:
            def log_stream():
                for chunk in container.logs(stream=True, follow=True, tail=tail):
                    yield f"data: {chunk.decode('utf-8', errors='replace')}\n\n"
            return StreamingResponse(log_stream(), media_type="text/event-stream")
        else:
            logs = container.logs(tail=tail).decode("utf-8", errors="replace")
            return {"container": container_name, "logs": logs}
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8766))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("infra_manager:app", host=host, port=port, reload=False)
