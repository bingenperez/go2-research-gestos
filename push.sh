#!/usr/bin/env bash
# push.sh — sube la imagen a ghcr.io/$GHCR_USER/go2-research-gestos
#
# Antes, una sola vez (token de GitHub con permiso write:packages):
#   echo <TOKEN> | docker login ghcr.io -u <tu usuario> --password-stdin
#
# USO
#   ./build.sh && ./push.sh
#
# El paquete nace PRIVADO en GitHub. Para que otros hagan `docker pull` sin
# login: GitHub -> tu perfil -> Packages -> go2-research-gestos ->
# Package settings -> Change visibility -> Public.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

[[ -f .env ]] && GHCR_USER="${GHCR_USER:-$(sed -n 's/^GHCR_USER=//p' .env | tail -1)}"
GHCR_USER="${GHCR_USER:-bingenperez}"
GHCR_USER="${GHCR_USER,,}"
IMAGE="ghcr.io/${GHCR_USER}/go2-research-gestos"

if [[ "${GHCR_USER}" == "tu-usuario" ]]; then
    echo "ERROR: falta tu usuario de GitHub. Ponlo en .env:  GHCR_USER=<usuario>"
    echo "       (o ejecuta: GHCR_USER=<usuario> ./push.sh). Luego ./build.sh otra vez."
    exit 1
fi

# Todas las etiquetas locales de la imagen (latest + fechas)
mapfile -t TAGS < <(docker images "${IMAGE}" --format '{{.Tag}}' | grep -v '<none>')
if [[ ${#TAGS[@]} -eq 0 ]]; then
    echo "ERROR: no hay ninguna imagen ${IMAGE} en local. Ejecuta antes ./build.sh"
    exit 1
fi

for t in "${TAGS[@]}"; do
    echo "subiendo ${IMAGE}:${t}"
    docker push "${IMAGE}:${t}"
done
echo
echo "hecho. Para descargarla en otro PC:  docker pull ${IMAGE}:latest"
