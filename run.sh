#!/usr/bin/env bash
# run.sh — entra en el contenedor en modo ROBOT REAL, con ventanas (--show).
#
# Equivale a lo que antes se hacia a mano:
#   xhost +local:docker
#   docker compose -f docker-compose.yml -f docker-compose.linux.yml \
#       --profile real run --rm -e GO2_GUI=x11 -e DISPLAY=$DISPLAY \
#       -e QT_X11_NO_MITSHM=1 -v /tmp/.X11-unix:/tmp/.X11-unix:rw real bash
#
# USO
#   ./run.sh                 usa GO2_IFACE de .env
#   ./run.sh enp3s0          usa esa interfaz (la del cable al robot)
#   ./run.sh enp3s0 python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --help
#
# Para el simulador o el perfil dev sigue valiendo ./go2 (sin cambios).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "El modo robot real necesita Linux (network_mode: host)."; exit 1
fi

if [[ $# -ge 1 && -e "/sys/class/net/$1" ]]; then
    export GO2_IFACE="$1"; shift
elif [[ $# -ge 1 && "$1" != -* && "$1" != python* && "$1" != bash ]]; then
    echo "ERROR: la interfaz '$1' no existe en este PC. Las que hay:"
    ip -br link | awk '$1 != "lo" {print "  " $1 "  " $2}'
    exit 1
fi
if [[ -z "${GO2_IFACE:-}" && -f .env ]]; then
    GO2_IFACE="$(sed -n 's/^GO2_IFACE=//p' .env | tail -1)"; export GO2_IFACE
fi
if [[ -z "${GO2_IFACE:-}" ]]; then
    echo "ERROR: dime la interfaz del cable al robot:  ./run.sh <interfaz>"
    echo "       (o pon GO2_IFACE=<interfaz> en .env). Las que hay:"
    ip -br link | awk '$1 != "lo" {print "  " $1 "  " $2}'
    exit 1
fi

FICHEROS=(-f docker-compose.yml)
if compgen -G "/dev/dri/renderD*" >/dev/null 2>&1 && [[ -f docker-compose.linux.yml ]]; then
    FICHEROS+=(-f docker-compose.linux.yml)
fi

# Permiso para abrir ventanas en la pantalla del PC. Se retira al salir.
if command -v xhost >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    xhost +local:docker >/dev/null 2>&1 || true
    trap 'xhost -local:docker >/dev/null 2>&1 || true' EXIT
fi

[[ $# -eq 0 ]] && set -- bash
echo "interfaz del robot: ${GO2_IFACE}   pantalla: ${DISPLAY:-ninguna}"
docker compose "${FICHEROS[@]}" --profile real run --rm \
    -e GO2_IFACE="${GO2_IFACE}" \
    -e GO2_GUI=x11 -e DISPLAY="${DISPLAY:-:0}" -e QT_X11_NO_MITSHM=1 \
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
    real "$@"
