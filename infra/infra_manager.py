"""
Lumio Infrastructure Manager — Kubernetes Edition
FastAPI service that manages K8s Deployments in the lumio namespace.
Runs as a pod inside K3s with a ServiceAccount.
Exposed via NodePort to LAN so web monitor on Windows PC can reach it.

Scale up   = replicas 0 → 1  (start)
Scale down = replicas 1 → 0  (stop)
"""

import os
import logging
from datetime import datetime
from typing import Optional

import yaml as pyyaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NAMESPACE = os.environ.get("LUMIO_NAMESPACE", "lumio")
SERVICES_CONFIG_PATH = os.environ.get("SERVICES_CONFIG", "/app/configs/services.yaml")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("infra_manager")

# ---------------------------------------------------------------------------
# Kubernetes client — in-cluster when running as pod
# ---------------------------------------------------------------------------
try:
    config.load_incluster_config()
    log.info("Loaded in-cluster Kubernetes config.")
except config.ConfigException:
    try:
        config.load_kube_config()
        log.info("Loaded kubeconfig from file.")
    except Exception as e:
        log.error(f"Cannot load Kubernetes config: {e}")

apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()

# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------
def load_services_config() -> dict:
    try:
        with open(SERVICES_CONFIG_PATH) as f:
            return pyyaml.safe_load(f)
    except FileNotFoundError:
        return {"services": {}}


def deployment_name(service_id: str, meta: dict = None) -> str:
    """Resolve K8s deployment name. Uses 'deployment' field from services.yaml if present."""
    if meta and meta.get("deployment"):
        return meta["deployment"]
    return service_id


# ---------------------------------------------------------------------------
# K8s helpers
# ---------------------------------------------------------------------------
def get_deployment_status(name: str) -> dict:
    try:
        dep = apps_v1.read_namespaced_deployment(name=name, namespace=NAMESPACE)
        replicas = dep.spec.replicas or 0
        ready = dep.status.ready_replicas or 0
        running = replicas > 0 and ready > 0
        return {
            "name": name,
            "replicas": replicas,
            "ready_replicas": ready,
            "running": running,
            "status": "running" if running else ("starting" if replicas > 0 else "stopped"),
        }
    except ApiException as e:
        if e.status == 404:
            return {"name": name, "running": False, "status": "not_deployed"}
        return {"name": name, "running": False, "status": "error", "error": str(e)}


def scale_deployment(name: str, replicas: int) -> dict:
    try:
        patch = {"spec": {"replicas": replicas}}
        apps_v1.patch_namespaced_deployment(
            name=name,
            namespace=NAMESPACE,
            body=patch,
        )
        action = "started" if replicas > 0 else "stopped"
        log.info(f"Scaled {name} to {replicas} replicas → {action}")
        return {"status": action, "deployment": name, "replicas": replicas}
    except ApiException as e:
        if e.status == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Deployment '{name}' not found in namespace '{NAMESPACE}'. Run deploy.sh first."
            )
        raise HTTPException(status_code=500, detail=str(e))


def get_pod_logs(deployment_name: str, tail: int = 100) -> str:
    try:
        pods = core_v1.list_namespaced_pod(
            namespace=NAMESPACE,
            label_selector=f"app={deployment_name}",
        )
        if not pods.items:
            return f"No pods found for deployment '{deployment_name}'"
        pod = pods.items[0]
        logs = core_v1.read_namespaced_pod_log(
            name=pod.metadata.name,
            namespace=NAMESPACE,
            tail_lines=tail,
        )
        return logs
    except ApiException as e:
        return f"Error fetching logs: {e}"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Lumio Infrastructure Manager",
    description="Controls K8s Deployments for the Lumio home learning platform",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    try:
        apps_v1.list_namespaced_deployment(namespace=NAMESPACE, limit=1)
        k8s_ok = True
    except Exception:
        k8s_ok = False
    return {
        "status": "ok" if k8s_ok else "degraded",
        "kubernetes": k8s_ok,
        "namespace": NAMESPACE,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Service list
# ---------------------------------------------------------------------------
def _resolve_dep(service_name: str, config_data: dict = None) -> str:
    """Get K8s deployment name for a service ID, respecting 'deployment' field in config."""
    if config_data is None:
        config_data = load_services_config()
    meta = config_data.get("services", {}).get(service_name, {})
    return deployment_name(service_name, meta)


@app.get("/services")
def list_services():
    config_data = load_services_config()
    services = config_data.get("services", {})
    result = []
    for svc_id, meta in services.items():
        dep_name = deployment_name(svc_id, meta)
        status = get_deployment_status(dep_name)
        result.append({
            "id": svc_id,
            "display_name": meta.get("display_name", svc_id),
            "description": meta.get("description", ""),
            "port": meta.get("port"),
            "deployment": dep_name,
            **status,
        })
    return {"services": result, "count": len(result)}


@app.get("/services/{service_name}")
def get_service(service_name: str):
    config_data = load_services_config()
    if service_name not in config_data.get("services", {}):
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not in registry")
    meta = config_data["services"][service_name]
    dep_name = deployment_name(service_name, meta)
    status = get_deployment_status(dep_name)
    return {
        "id": service_name,
        "display_name": meta.get("display_name", service_name),
        "description": meta.get("description", ""),
        "port": meta.get("port"),
        "deployment": dep_name,
        **status,
    }


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------
@app.post("/services/{service_name}/start")
def start_service(service_name: str):
    return scale_deployment(_resolve_dep(service_name), replicas=1)


@app.post("/services/{service_name}/stop")
def stop_service(service_name: str):
    return scale_deployment(_resolve_dep(service_name), replicas=0)


@app.post("/services/{service_name}/restart")
def restart_service(service_name: str):
    dep = _resolve_dep(service_name)
    scale_deployment(dep, replicas=0)
    import time; time.sleep(2)
    return scale_deployment(dep, replicas=1)


@app.post("/services/start-all")
def start_all():
    config_data = load_services_config()
    results = {}
    for svc_id, meta in config_data.get("services", {}).items():
        try:
            scale_deployment(deployment_name(svc_id, meta), replicas=1)
            results[svc_id] = "started"
        except Exception as e:
            results[svc_id] = f"error: {e}"
    return {"results": results}


@app.post("/services/stop-all")
def stop_all():
    config_data = load_services_config()
    results = {}
    for svc_id, meta in config_data.get("services", {}).items():
        try:
            scale_deployment(deployment_name(svc_id, meta), replicas=0)
            results[svc_id] = "stopped"
        except Exception as e:
            results[svc_id] = f"error: {e}"
    return {"results": results}


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
@app.get("/services/{service_name}/logs")
def get_logs(service_name: str, tail: int = 100):
    dep_name = _resolve_dep(service_name)
    logs = get_pod_logs(dep_name, tail=tail)
    return {"deployment": dep_name, "logs": logs}


# ---------------------------------------------------------------------------
# Entry point (for local dev — in K8s it's run by uvicorn in Dockerfile)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8766))
    uvicorn.run("infra_manager:app", host="0.0.0.0", port=port, reload=False)
