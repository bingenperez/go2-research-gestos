#!/usr/bin/env bash
# build.sh — construye la imagen de go2-research-gestos (docker/Dockerfile.gestos)
#
# Parte de la imagen go2-workspace fijada por digest y solo le anade MediaPipe,
# sounddevice y el modelo de pose. NO toca ni reconstruye la imagen original.
#
# USO
#   ./build.sh                 -> ghcr.io/$GHCR_USER/go2-research-gestos:latest (+ :<fecha>)
#   GHCR_USER=otro ./build.sh  -> otro usuario/organizacion de GitHub
#
# GHCR_USER se lee del entorno o de .env (por defecto: el de docker-compose.yml).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

[[ -f .env ]] && GHCR_USER="${GHCR_USER:-$(sed -n 's/^GHCR_USER=//p' .env | tail -1)}"
GHCR_USER="${GHCR_USER:-tu-usuario}"
GHCR_USER="${GHCR_USER,,}"                     # ghcr exige minusculas
IMAGE="ghcr.io/${GHCR_USER}/go2-research-gestos"
TAG_FECHA="$(date +%Y%m%d)"

echo "construyendo ${IMAGE}:latest y ${IMAGE}:${TAG_FECHA}"
docker build \
    -f docker/Dockerfile.gestos \
    -t "${IMAGE}:latest" \
    -t "${IMAGE}:${TAG_FECHA}" \
    docker/

echo
docker run --rm --entrypoint tail "${IMAGE}:latest" -n 6 /opt/go2/VERSIONS.txt
echo
echo "listo: ${IMAGE}:latest"
[[ "${GHCR_USER}" == "tu-usuario" ]] && \
    echo "AVISO: GHCR_USER no configurado. Pon GHCR_USER=<tu usuario de GitHub> en .env antes de ./push.sh"
exit 0
