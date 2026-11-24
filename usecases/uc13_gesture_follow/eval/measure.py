#!/usr/bin/env python3
"""usecases/uc13_gesture_follow/eval/measure.py

Mide un experimento ya registrado y anota el resultado en su manifest.json.

    deploy/gesture_follow.py --log ->  experiments/uc13_gesture_follow/<run>/{manifest.json, metrics.csv}
    eval/measure.py                  ->  lee metrics.csv y escribe "resultados" en el manifest

Copia de la de uc12 con las métricas del seguimiento. Mismo esquema que la
plantilla uc00: medir separado de ejecutar, y sin sha de git en el manifest el
experimento no es trazable y no se mide.

Uso:
    python3 usecases/uc13_gesture_follow/eval/measure.py                  # el ultimo
    python3 usecases/uc13_gesture_follow/eval/measure.py --run experiments/uc13_gesture_follow/<run>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from go2core import paths  # noqa: E402

UC = Path(__file__).resolve().parents[1]
USECASE = UC.name


def ultimo_run() -> Path:
    runs = sorted(d for d in (paths.EXPERIMENTS / USECASE).glob("*")
                  if (d / "manifest.json").exists())
    if not runs:
        raise SystemExit(f"No hay experimentos en experiments/{USECASE}/. "
                         f"Ejecuta antes deploy/gesture_follow.py con --log.")
    return runs[-1]


def cargar(run: Path) -> tuple[dict, list[dict]]:
    manifest_path = run / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"{run} no tiene manifest.json: no es trazable.")
    manifest = json.loads(manifest_path.read_text())
    sha = (manifest.get("git") or {}).get("sha")
    if not sha or sha == "unknown":
        raise SystemExit("El manifest no tiene sha de git: no es trazable.")
    with open(run / "metrics.csv") as fh:
        filas = list(csv.DictReader(fh))
    if not filas:
        raise SystemExit("metrics.csv esta vacio.")
    return manifest, filas


def medir(filas: list[dict]) -> dict:
    t = [float(f["t"]) for f in filas]
    huecos = [float(f["edad_imagen"]) for f in filas]
    confirmados = [f for f in filas if f["gesto_confirmado"]]
    lanzadas = Counter(f["accion"] for f in confirmados
                       if f["accion"] not in ("", "nada") and f["resultado"] != "imagen_vieja")
    resultados = Counter(f"{f['accion']}:{f['resultado']}" for f in confirmados)
    return {
        "fotogramas": len(filas),
        "duracion_s": round(t[-1] - t[0], 2),
        "persona_pct": round(100 * sum(int(f["persona"]) for f in filas) / len(filas), 1),
        "gestos_brutos": dict(Counter(f["gesto_bruto"] for f in filas)),
        "gestos_confirmados": len(confirmados),
        "acciones_lanzadas": dict(lanzadas),
        "resultados": dict(resultados),
        "huecos_imagen_mayores_05s": sum(h > 0.5 for h in huecos),
        "hueco_imagen_max_s": round(max(huecos), 3),
        "estados_robot": dict(Counter(f["estado_robot"] for f in filas)),
        **medir_saludo(filas),
        **medir_seguimiento(filas),
    }


def medir_saludo(filas: list[dict]) -> dict:
    """Para ajustar los umbrales del saludo: lo que midió la ventana cuando
    lo detectó y cuando no. Solo en los CSV con las columnas saludo_*."""
    if not filas or "saludo_cambios" not in filas[0]:
        return {}
    con = [f for f in filas if f["gesto_bruto"] == "saludo"]
    sin = [f for f in filas if f["gesto_bruto"] != "saludo" and f["saludo_lado"]]

    def maximos(fs):
        return {"cambios_max": max((int(f["saludo_cambios"]) for f in fs), default=0),
                "amplitud_max": round(max((float(f["saludo_amplitud"]) for f in fs),
                                          default=0.0), 3)}
    return {"saludo": {
        "fotogramas_detectado": len(con),
        "fotogramas_brazo_alzado_sin_saludo": len(sin),
        "detectado": maximos(con),
        "alzado_sin_saludo": maximos(sin),
        "confirmados": sum(1 for f in filas if f["gesto_confirmado"] == "saludo"),
    }}


def medir_seguimiento(filas: list[dict]) -> dict:
    """Métricas de la marcha. Las mismas que se usaron para el diagnóstico del
    2026-11-18: % del tiempo andando por debajo de 0.2 m/s (marcha de pasitos
    de Sport Mode) y arranques por minuto. Vale también para los CSV antiguos
    (sin las columnas nuevas)."""
    sig = [f for f in filas if f["modo"] in ("SIGUIENDO", "PAUSADO")]
    dist = sorted(float(f["distancia_m"]) for f in sig
                  if f["modo"] == "SIGUIENDO" and f["distancia_m"])
    vx = [float(f["vx"]) for f in sig]
    adelante = [v for v in vx if v > 0]
    arranques = sum(1 for a, b in zip(vx, vx[1:]) if a == 0 and b != 0)
    dur = (float(sig[-1]["t"]) - float(sig[0]["t"])) if len(sig) > 1 else 0.0
    wz = [float(f["wz"]) for f in sig]
    giros = sum(1 for a, b in zip(wz, wz[1:]) if a == 0 and b != 0)
    res = {
        "modos": dict(Counter(f["modo"] for f in filas)),
        "fotogramas_siguiendo": len(sig),
        "vx_max": round(max((float(f["vx"]) for f in filas), default=0.0), 3),
        "vx_min": round(min((float(f["vx"]) for f in filas), default=0.0), 3),
        "wz_max_abs": round(max((abs(float(f["wz"])) for f in filas), default=0.0), 3),
        "distancia_mediana_siguiendo_m": dist[len(dist) // 2] if dist else None,
        "fotogramas_watchdog": sum(int(f["watchdog"]) for f in filas),
        "andando_bajo_0_2_pct": (round(100 * sum(v < 0.2 - 1e-6 for v in adelante)
                                       / len(adelante), 1) if adelante else None),
        "arranques_por_min": round(60 * arranques / dur, 1) if dur else None,
        "giros_por_min": round(60 * giros / dur, 1) if dur else None,
    }
    if filas and "cuerpo" in filas[0]:
        res["cuerpo"] = dict(Counter(f["cuerpo"] for f in filas))
        # pitch_cmd: radianes con signo (antes del 2026-11-18 tarde, grados).
        res["pitch_cmd_max"] = max((abs(float(f["pitch_cmd"])) for f in filas), default=0.0)
        res["fotogramas_marcha_atras"] = sum(int(f["marcha_atras"]) for f in filas)
        alt = [float(f["body_height"]) for f in sig if f["body_height"]]
        res["body_height_siguiendo"] = [min(alt), max(alt)] if alt else None
    return res


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", default=None, help="carpeta del experimento (por defecto, el ultimo)")
    args = p.parse_args()

    run = Path(args.run).resolve() if args.run else ultimo_run()
    manifest, filas = cargar(run)
    res = medir(filas)

    manifest["resultados"] = res
    (run / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    print(f"experimento: {run.name}")
    print(f"git sha    : {manifest['git']['sha'][:7]}"
          f"{'  (con cambios sin commitear: no reproducible)' if manifest['git'].get('dirty') else ''}")
    for k, v in res.items():
        print(f"  {k:<26} {v}")
    print(f"\nanotado en {run / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
