#!/usr/bin/env bash
# tools/get_models.sh — descarga los pesos que git no guarda (*.pt en .gitignore)
#
#   yolo11n.pt  (raiz del repo)  lo usan uc13 (configs/gesture_follow.yaml,
#                                yolo.modelo) y uc04. Sin el, uc13 con camara
#                                falla con "No existe el modelo YOLO".
#
# El modelo de pose de MediaPipe NO hace falta: ya viene dentro de la imagen.
# Se comprueba el sha256: es el mismo fichero con el que uc13 funciono en el
# robot real. Si ya existe y es correcto, no hace nada. run.sh lo llama solo.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

URL="https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt"
SHA="0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
DEST="yolo11n.pt"

if [[ -f "${DEST}" ]] && echo "${SHA}  ${DEST}" | sha256sum -c --status -; then
    echo "[ok] ${DEST} ya esta (sha256 correcto)"; exit 0
fi

echo "descargando ${DEST} (5,4 MB) ..."
TMP="$(mktemp "${DEST}.XXXX")"
trap 'rm -f "${TMP}"' EXIT
if ! curl -fSL --retry 2 -o "${TMP}" "${URL}"; then
    echo "[x] no se ha podido descargar. Necesitas internet (Wi-Fi) para este paso."
    exit 1
fi
if ! echo "${SHA}  ${TMP}" | sha256sum -c --status -; then
    echo "[x] el fichero descargado no es el esperado (sha256 distinto). No se usa."
    exit 1
fi
mv "${TMP}" "${DEST}"; chmod 644 "${DEST}"
echo "[ok] ${DEST} descargado y comprobado"
