#!/bin/bash
# Lumio — Build all Docker images and import into K3s
# Run this on the Debian node inside agent-ecosystem/infra/
# Usage: bash build.sh

set -e

INFRA_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== Lumio Build Script ==="
echo "Working from: $INFRA_DIR"
echo ""

build_and_import() {
  local name=$1
  local context=$2
  local dockerfile=${3:-"$context/Dockerfile"}

  echo "--- Building $name ---"
  docker build -t "$name:latest" -f "$dockerfile" "$context"
  echo "--- Importing $name into K3s ---"
  docker save "$name:latest" | sudo k3s ctr images import -
  echo "--- $name done ---"
  echo ""
}

# infra-manager (context is infra/ root, custom Dockerfile)
build_and_import "lumio-infra-manager" "$INFRA_DIR" "$INFRA_DIR/Dockerfile.infra_manager"

# services
build_and_import "lumio-storage"      "$INFRA_DIR/services/storage-service"
build_and_import "lumio-rag"          "$INFRA_DIR/services/rag-service"
build_and_import "lumio-tutor"        "$INFRA_DIR/services/tutor-service"
build_and_import "lumio-time-watcher" "$INFRA_DIR/services/time-watcher"
build_and_import "lumio-ui"           "$INFRA_DIR/services/ui-service"

echo "=== All images built and imported ==="
echo "Verify with: sudo k3s ctr images list | grep lumio"
