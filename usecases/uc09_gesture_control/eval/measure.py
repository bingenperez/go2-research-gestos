#!/usr/bin/env python3
"""usecases/uc09_gesture_control/eval/measure.py

Mide un experimento ya registrado y anota el resultado en su manifest.json.

    deploy/gesture_control.py --log  ->  experiments/uc09_gesture_control/<run>/{manifest.json, metrics.csv}
    eval/measure.py                  ->  lee metrics.csv y escribe "resultados" en el manifest

Mismo esquema que la plantilla uc00: medir separado de ejecutar, y sin sha de
git en el manifest el experimento no es trazable y no se mide.

Uso:
    python3 usecases/uc09_gesture_control/eval/measure.py                  # el ultimo
    python3 usecases/uc09_gesture_control/eval/measure.py --run experiments/uc09_gesture_control/<run>
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
                         f"Ejecuta antes deploy/gesture_control.py con --log.")
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
    }


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
