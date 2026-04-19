#!/bin/bash
# Lumio — Deploy all K8s manifests to K3s
# Run this on the Debian node inside agent-ecosystem/infra/
# Usage: bash deploy.sh

set -e

K8S_DIR="$(cd "$(dirname "$0")" && pwd)/k8s"
echo "=== Lumio Deploy Script ==="
echo "Manifests from: $K8S_DIR"
echo ""

# 1. Namespace first
echo "--- Namespace ---"
sudo kubectl apply -f "$K8S_DIR/namespace.yaml"

# 2. RBAC
echo "--- RBAC ---"
sudo kubectl apply -f "$K8S_DIR/rbac.yaml"

# 3. ConfigMap — UPDATE OLLAMA IP IN configmap.yaml BEFORE RUNNING
echo "--- ConfigMap ---"
sudo kubectl apply -f "$K8S_DIR/configmap.yaml"

# 4. PVCs
echo "--- PVCs ---"
sudo kubectl apply -f "$K8S_DIR/rag-service/pvc.yaml"
sudo kubectl apply -f "$K8S_DIR/time-watcher/pvc.yaml"

# 5. Services (all deployments)
echo "--- Storage Service ---"
sudo kubectl apply -f "$K8S_DIR/storage-service/deployment.yaml"
sudo kubectl apply -f "$K8S_DIR/storage-service/service.yaml"

echo "--- RAG Service ---"
sudo kubectl apply -f "$K8S_DIR/rag-service/deployment.yaml"
sudo kubectl apply -f "$K8S_DIR/rag-service/service.yaml"

echo "--- Tutor Service ---"
sudo kubectl apply -f "$K8S_DIR/tutor-service/deployment.yaml"
sudo kubectl apply -f "$K8S_DIR/tutor-service/service.yaml"

echo "--- Time Watcher ---"
sudo kubectl apply -f "$K8S_DIR/time-watcher/deployment.yaml"
sudo kubectl apply -f "$K8S_DIR/time-watcher/service.yaml"

echo "--- UI Service ---"
sudo kubectl apply -f "$K8S_DIR/ui-service/deployment.yaml"
sudo kubectl apply -f "$K8S_DIR/ui-service/service.yaml"
sudo kubectl apply -f "$K8S_DIR/ui-service/ingress.yaml"

echo "--- Infra Manager ---"
sudo kubectl apply -f "$K8S_DIR/infra-manager/deployment.yaml"
sudo kubectl apply -f "$K8S_DIR/infra-manager/service.yaml"

echo ""
echo "=== Deploy complete ==="
echo "Checking pods (give it 30s to pull/start):"
sleep 5
sudo kubectl get pods -n lumio
echo ""
echo "Access points:"
echo "  Kids UI:       http://lumio.local  (add 192.168.1.62 lumio.local to /etc/hosts on each device)"
echo "  Infra Manager: http://192.168.1.62:30766"
echo "  Infra Health:  http://192.168.1.62:30766/health"
