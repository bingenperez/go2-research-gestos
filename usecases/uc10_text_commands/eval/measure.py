#!/usr/bin/env python3
"""usecases/uc10_text_commands/eval/measure.py

Mide un experimento ya registrado y anota el resultado en su manifest.json.

    deploy/text_commands.py --log  ->  experiments/uc10_text_commands/<run>/{manifest.json, metrics.csv}
    eval/measure.py                ->  lee metrics.csv y escribe "resultados" en el manifest

Mismo esquema que uc00 y uc09: sin sha de git en el manifest el experimento no
es trazable y no se mide.

Uso:
    python3 usecases/uc10_text_commands/eval/measure.py                  # el ultimo
    python3 usecases/uc10_text_commands/eval/measure.py --run experiments/uc10_text_commands/<run>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from go2core import paths  # noqa: E402

UC = Path(__file__).resolve().parents[1]
USECASE = UC.name
MOVIMIENTO = ("levantar", "tumbar", "saludar", "sentar")


def ultimo_run() -> Path:
    runs = sorted(d for d in (paths.EXPERIMENTS / USECASE).glob("*")
                  if (d / "manifest.json").exists())
    if not runs:
        raise SystemExit(f"No hay experimentos en experiments/{USECASE}/. "
                         f"Ejecuta antes deploy/text_commands.py con --log.")
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
    duraciones: dict[str, list[float]] = defaultdict(list)
    for f in filas:
        if f["orden"] in MOVIMIENTO and f["resultado"] == "ok":
            duraciones[f["orden"]].append(float(f["duracion_s"]))
    mov = [f for f in filas if f["orden"] in MOVIMIENTO]
    return {
        "ordenes": len(filas),
        "por_orden": dict(Counter(f["orden"] or "no_entendida" for f in filas)),
        "movimiento_ok": sum(f["resultado"] == "ok" for f in mov),
        "movimiento_total": len(mov),
        "resultados": dict(Counter(f"{f['orden'] or '?'}:{f['resultado']}" for f in filas)),
        "duracion_media_s": {o: round(sum(d) / len(d), 2) for o, d in duraciones.items()},
        "llamadas": dict(Counter(ll for f in filas for ll in f["llamadas"].split("+") if ll)),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", default=None, help="carpeta del experimento (por defecto, el ultimo)")
    args = p.parse_args()

    run = Path(args.run).resolve() if args.run else ultimo_run()
    manifest, filas = cargar(run)
    res = medir(filas)

    manifest["medidas"] = res
    (run / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    print(f"experimento: {run.name}")
    print(f"git sha    : {manifest['git']['sha'][:7]}"
          f"{'  (con cambios sin commitear: no reproducible)' if manifest['git'].get('dirty') else ''}")
    for k, v in res.items():
        print(f"  {k:<20} {v}")
    print(f"\nanotado en {run / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
