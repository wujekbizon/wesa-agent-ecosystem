# Lumio — Home Learning RAG Platform

Local home network AI learning platform for kids.
Each child uploads their own study materials. The LLM only uses those materials to help them learn.
Greg controls all services from the WESA web monitor.

---

## Architecture

```
Greg's Windows PC (web monitor + Ollama + WESA daemon)
    │
    │  LAN HTTP  (192.168.1.50:8766)
    ▼
Ubuntu VM (VMware)
    └── Docker Compose
          ├── infra-manager   :8766  ← Greg's control plane
          ├── storage         :8767  ← file uploads per user
          ├── rag             :8768  ← ChromaDB + ingestion + query
          ├── tutor           :8769  ← conversational AI tutor
          ├── time-watcher    :8770  ← study session tracking
          └── ui              :3000  ← kids' web interface (LAN)
```

---

## Setup

### 1. Ubuntu VM (one-time)
```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Install Docker Compose
sudo apt install docker-compose-plugin

# Create Linux user per kid
sudo useradd -m -s /bin/bash emma
sudo useradd -m -s /bin/bash jan
# (repeat per kid)

# Clone agent-ecosystem repo
git clone https://github.com/wujekbizon/wesa-agent-ecosystem.git
cd wesa-agent-ecosystem/infra
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env — set UBUNTU_VM_IP and WINDOWS_PC_IP (Ollama address)
```

### 3. Pull Ollama models (on Windows PC)
```powershell
ollama pull nomic-embed-text   # embeddings
ollama pull qwen2.5            # tutor LLM
```

### 4. Start services
```bash
docker compose up -d
docker compose ps   # verify all running
```

### 5. Add Infra tab to web monitor (on Windows PC)
- Add `LUMIO_INFRA_MANAGER_URL=http://192.168.1.50:8766` to `agent-ecosystem/.env`
- Merge `infra/web_monitor_infra_patch.py` routes into `web_monitor.py`
- Merge `infra/dashboard_infra_tab.js` into `static/dashboard.js`

---

## K8s Migration (when Proxmox is back)
```bash
cd infra
kompose convert -o k8s/
# Review generated manifests in k8s/
kubectl apply -f k8s/
```

---

## Files
```
infra/
├── infra_manager.py              Control plane (FastAPI + Docker SDK)
├── docker-compose.yml            All services
├── Dockerfile.infra_manager      Image for control plane
├── .env.example                  Template — copy to .env
├── configs/
│   ├── services.yaml             Service registry
│   └── network.yaml              LAN IPs and ports
├── services/
│   ├── storage-service/          File upload + per-user folders
│   ├── rag-service/              ChromaDB + ingestion + query
│   ├── tutor-service/            Ollama conversational tutor
│   ├── time-watcher/             Study session tracking
│   └── ui-service/               Kids' web interface
├── web_monitor_infra_patch.py    Routes to merge into web_monitor.py
├── dashboard_infra_tab.js        JS/HTML/CSS to merge into dashboard
└── k8s/                          K8s manifests (generated, empty for now)
```
