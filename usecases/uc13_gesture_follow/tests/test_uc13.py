"""usecases/uc13_gesture_follow/tests/test_uc13.py

Pruebas de uc13 SIN ROBOT, sin MediaPipe y sin YOLO: esqueletos sintéticos,
persona (YOLO) falsa, cámara simulada (repetidos y congelada), estado del
robot falso con IMU, y SportClient falso que registra cada llamada con su
hora. El bucle principal se prueba por el camino REAL (sin --dry-run), con el
"si" contestado por la prueba. No abre DDS. Lo que escribe va a un directorio
temporal, nunca al repo.

    1. gestos: tabla, dos manos / una mano excluyentes, ruido
    2. filtro y distancia
    3. control: histéresis, marcha suave, marcha atrás limitada, giro, topes
    4. inclinación (unidad): reenvío cada 0.1 s, tiempos, desactivación
    5. confirmador: re-armado por gesto
    6. Robot: gesto x estado x modo, lista blanca, rango de pie activo
    7. main(): interruptor, parar sin cooldown, gesto pendiente en el
       cooldown, pérdida (y cabeza fuera sin pérdida), cámara congelada,
       estado viejo, histéresis, marcha atrás, inclinación (solo en reposo,
       Euler cada 0.1 s, recto 0.5 s antes de Move, quieto de pie), tumbar y
       cierre inclinado, body_height al andar, IMU que no cambia,
       --probar-inclinacion, dry-run, --log
    8. repetición del control nuevo sobre los metrics.csv reales de hoy

Uso, dentro de la caja (no necesita mediapipe ni YOLO; tarda ~3 min):

    python3 usecases/uc13_gesture_follow/tests/test_uc13.py

O desde fuera, sin red y con el repo en solo lectura:

    docker run --rm --network none -v "$PWD":/workspace:ro -w /workspace \\
        -e PYTHONDONTWRITEBYTECODE=1 ghcr.io/isoto-mondragon/go2-workspace:latest \\
        python3 usecases/uc13_gesture_follow/tests/test_uc13.py

Sale con código 0 si todo pasa y 1 si algo falla.
"""
from __future__ import annotations

import builtins
import copy
import csv
import glob
import io
import json
import math
import sys
import tempfile
import threading
import time
import types
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import yaml

UC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UC))
sys.path.insert(0, str(UC / "deploy"))
sys.path.insert(0, str(UC.parents[1] / "src"))

TMP = Path(tempfile.mkdtemp(prefix="uc13_tests_"))

from perception import pose as P  # noqa: E402
from perception.personas import Persona, elegir  # noqa: E402
from control import seguimiento as S  # noqa: E402
from control import inclinacion as I  # noqa: E402

CFG = yaml.safe_load((UC / "configs" / "gesture_follow.yaml").read_text())
G = CFG["gestos"]
CS = CFG["seguimiento"]
# La logica de la inclinacion se prueba ACTIVA aunque el yaml la desactive.
# El caso Q prueba la configuracion con activa: false.
ACTIVA_YAML = bool(CFG["inclinacion"].get("activa", True))
CFG["inclinacion"]["activa"] = True
CI = CFG["inclinacion"]

FALLOS: list[str] = []
N_OK = 0


def check(cond: bool, nombre: str) -> None:
    global N_OK
    if cond:
        N_OK += 1
    else:
        FALLOS.append(nombre)
        print(f"  FALLO: {nombre}")


# ===========================================================================
# Esqueletos sintéticos
# ===========================================================================
def esqueleto(gesto: str, *, cx=0.5, hy=0.45, a=0.06, torso=2.5, espejo=False,
              angulo=0.0, brazo=1.6, nariz=True, ocultar=(), perfil=False) -> np.ndarray:
    p = np.zeros((33, 3), np.float32)
    s = -1.0 if espejo else 1.0
    ancho = 0.0 if perfil else a
    xi, xd = cx + s * ancho / 2, cx - s * ancho / 2
    fi, fd = np.sign(xi - cx) or 1.0, np.sign(xd - cx) or -1.0
    cad_y = hy + torso * a

    def put(i, x, y, v=0.95):
        p[i] = (x, y, v)
    put(P.HOMBRO_I, xi, hy); put(P.HOMBRO_D, xd, hy)
    put(P.CADERA_I, cx + s * 0.4 * a, cad_y); put(P.CADERA_D, cx - s * 0.4 * a, cad_y)
    put(P.TOBILLO_I, cx + s * 0.4 * a, cad_y + 2.2 * a)
    put(P.TOBILLO_D, cx - s * 0.4 * a, cad_y + 2.2 * a)
    if nariz:
        put(P.NARIZ, cx, hy - 0.75 * a)

    def brazo_en(fuera, h_x, ang, codo_i, mun_i):
        r = np.radians(ang)
        put(codo_i, h_x + fuera * brazo / 2 * a * np.cos(r), hy - brazo / 2 * a * np.sin(r))
        put(mun_i, h_x + fuera * brazo * a * np.cos(r), hy - brazo * a * np.sin(r))

    def abajo(h_x, fuera, codo_i, mun_i):
        put(codo_i, h_x + fuera * 0.1 * a, hy + 1.2 * a)
        put(mun_i, h_x + fuera * 0.15 * a, hy + 2.3 * a)

    def arriba(h_x, codo_i, mun_i):
        put(codo_i, h_x, hy - 0.9 * a); put(mun_i, h_x, hy - 1.9 * a)

    if gesto == "neutro":
        abajo(xi, fi, P.CODO_I, P.MUNECA_I); abajo(xd, fd, P.CODO_D, P.MUNECA_D)
    elif gesto == "mano_i":
        arriba(xi, P.CODO_I, P.MUNECA_I); abajo(xd, fd, P.CODO_D, P.MUNECA_D)
    elif gesto == "dos_manos":
        arriba(xi, P.CODO_I, P.MUNECA_I); arriba(xd, P.CODO_D, P.MUNECA_D)
    elif gesto == "subiendo":
        arriba(xi, P.CODO_I, P.MUNECA_I)
        put(P.CODO_D, xd + fd * 0.5 * a, hy + 0.3 * a); put(P.MUNECA_D, xd + fd * 0.3 * a, hy - 0.2 * a)
    elif gesto == "cruzados":
        put(P.CODO_I, xi + fi * 0.2 * a, hy + 0.9 * a)
        put(P.CODO_D, xd + fd * 0.2 * a, hy + 0.9 * a)
        put(P.MUNECA_I, cx + fd * 0.45 * a, hy + 0.6 * a)
        put(P.MUNECA_D, cx + fi * 0.45 * a, hy + 0.65 * a)
    elif gesto == "t":
        brazo_en(fi, xi, angulo, P.CODO_I, P.MUNECA_I)
        brazo_en(fd, xd, angulo, P.CODO_D, P.MUNECA_D)
    elif gesto == "t_y_mano":
        brazo_en(fi, xi, 0, P.CODO_I, P.MUNECA_I); arriba(xd, P.CODO_D, P.MUNECA_D)
    else:
        raise ValueError(gesto)
    for i in ocultar:
        p[i, 2] = 0.1
    return p


# ===========================================================================
print("\n== 1. gestos ==")
casos = [
    ("neutro", {}, P.NINGUNO), ("mano_i", {}, P.MANO), ("mano_i", {"espejo": True}, P.MANO),
    ("dos_manos", {}, P.DOS), ("dos_manos", {"espejo": True}, P.DOS),
    ("dos_manos", {"ocultar": (P.MUNECA_D,)}, P.NINGUNO),
    ("dos_manos", {"ocultar": (P.MUNECA_I,)}, P.NINGUNO),
    ("subiendo", {}, P.NINGUNO), ("dos_manos", {"nariz": False}, P.NINGUNO),
    ("cruzados", {}, P.CRUZADOS), ("t", {}, P.T), ("t", {"nariz": False}, P.T),
    ("t", {"angulo": 30}, P.NINGUNO), ("t_y_mano", {}, P.NINGUNO),
    ("t", {"perfil": True}, P.NINGUNO),
]
for g, kw, esperado in casos:
    p = esqueleto(g, **kw)
    check(P.clasificar(p, G) == esperado, f"gesto {g} {kw}: {P.clasificar(p, G)} != {esperado}")
rng = np.random.default_rng(13)
for gesto, g in ((P.DOS, "dos_manos"), (P.MANO, "mano_i"), (P.CRUZADOS, "cruzados"), (P.T, "t")):
    cuenta = dict.fromkeys((P.NINGUNO, P.DOS, P.MANO, P.CRUZADOS, P.T), 0)
    for _ in range(1500):
        a = rng.uniform(0.04, 0.10)
        p = esqueleto(g, a=a, torso=rng.uniform(1.5, 2.8), espejo=bool(rng.integers(2)))
        p[:, :2] += rng.normal(0, 0.15 * a, size=(33, 2))
        if rng.random() < 0.2:
            p[rng.choice([P.MUNECA_I, P.MUNECA_D]), 2] = 0.1
        cuenta[P.clasificar(p, G)] += 1
    check(sum(v for k, v in cuenta.items() if k not in (gesto, P.NINGUNO)) == 0,
          f"{gesto} con ruido se confunde: {cuenta}")
for _ in range(10000):
    p = esqueleto("neutro", a=rng.uniform(0.04, 0.10))
    for i in (P.CODO_I, P.CODO_D, P.MUNECA_I, P.MUNECA_D, P.NARIZ):
        p[i, :2] = rng.uniform(0.15, 0.85, size=2)
    check(not (P.dos_manos(p, G) and P.mano_levantada(p, G)), "dos manos y una mano a la vez")
print(f"  {len(casos)} casos, ruido sin confusiones, 10000 poses: dos manos y una mano nunca a la vez")


# ===========================================================================
print("\n== 1b. saludo (gesto dinamico) ==")
GS = G["saludo"]


def esqueleto_saludo(t, *, lado="i", f=2.0, pp=0.6, a=0.06, torso=1.4, espejo=False,
                     ruido=0.0, rng=None, dos=False, alzado=True, perfil=False):
    """Persona saludando: antebrazo vertical sobre el codo y la muñeca yendo
    y viniendo en horizontal, pp (pico a pico, en anchos de hombro) a f Hz.
    pp = 0: mano levantada quieta. alzado False: brazo abajo. torso 1.4:
    hombros -> caderas en anchos de hombro, como una persona real."""
    p = esqueleto("neutro", a=a, torso=torso, espejo=espejo, perfil=perfil)
    cx = 0.5
    lados = ("i", "d") if dos else (lado,)
    for ld in lados:
        hombro, codo, muneca, _ = P.LADOS[ld]
        hx, hy = float(p[hombro, 0]), float(p[hombro, 1])
        fuera = np.sign(hx - cx) or (1.0 if ld == "i" else -1.0)
        if not alzado:
            continue
        p[codo] = (hx + fuera * 0.4 * a, hy + 0.1 * a, 0.95)
        x = hx + fuera * 0.4 * a + pp / 2 * a * math.sin(2 * math.pi * f * t)
        p[muneca] = (x, hy - 0.8 * a, 0.95)
    if ruido and rng is not None:
        p[:, :2] += rng.normal(0, ruido * a, size=(33, 2))
    return p


def correr_saludo(fotos, dt=1 / 15, cfg=None):
    """fotos: lista de esqueletos (o None). Devuelve la lista de MedidaSaludo."""
    d = P.DetectorSaludo(cfg or G)
    return [d.paso(i * dt, p) for i, p in enumerate(fotos)]


# oscilacion() con series conocidas (a 15 fps un saludo de 2-3 Hz da 2-4
# fotogramas por tramo: mesetas de 2).
AMP_S = float(G["saludo"]["amplitud_min"])
vaiven = [0, 0, 0.5, 0.5, 0, 0, 0.5, 0.5, 0, 0, 0.5, 0.5, 0, 0]
check(P.oscilacion(vaiven, 0.1, AMP_S)[0] >= 3, f"oscilacion: {P.oscilacion(vaiven, 0.1, AMP_S)}")
check(P.oscilacion([0, 0.05, 0, 0.05, 0, 0.05, 0], 0.1)[0] == 0, "oscilacion: cuenta el temblor")
check(P.oscilacion([0, 0.1, 0.2, 0.3, 0.4], 0.1)[0] == 0, "oscilacion: rampa con cambios")
check(P.oscilacion([0, 0, 0.9, 0, 0, 0, 0], 0.1)[1] == 0.0, "oscilacion: un atipico pasa la mediana")
check(P.oscilacion([0, 0, 0, 0, 0.9], 0.1)[1] == 0.0, "oscilacion: atipico en el ULTIMO fotograma")
check(P.oscilacion([0, 0, 0.6, 0.6, 0, 0, 0], 0.1, AMP_S)[0] <= 1,
      "oscilacion: un atipico de 2 fotogramas suma mas de 1 cambio")
pequeno = [0, 0, 0.2, 0.2, 0, 0, 0.2, 0.2, 0, 0, 0.2, 0.2, 0, 0]
check(P.oscilacion(pequeno, 0.1, AMP_S)[0] == 0, "oscilacion: cuenta tramos menores que amplitud_min")
check(P.oscilacion([], 0.1) == (0, 0.0) and P.oscilacion([0.2], 0.1) == (0, 0.0), "oscilacion vacia")

# Brazo alzado: saludo si, T / cruzados / neutro no (la T queda fuera del saludo).
for g, esperado in (("neutro", False), ("t", False), ("cruzados", False)):
    p = esqueleto(g)
    esc = P.escala_saludo(p, GS, G["vis_min"])
    check(not any(P.brazo_alzado(p, GS, G["vis_min"], l, esc) for l in ("i", "d")),
          f"saludo: brazo alzado en {g}")
p = esqueleto_saludo(0.0)
esc = P.escala_saludo(p, GS, G["vis_min"])
check(P.brazo_alzado(p, GS, G["vis_min"], "i", esc)
      and not P.brazo_alzado(p, GS, G["vis_min"], "d", esc), "saludo: alzado mal")

FPS_S = 15
# Saludo real: con cualquiera de las dos manos, en espejo, 1.5-3 Hz, con temblor.
rng = np.random.default_rng(7)
t_det = []
for lado in ("i", "d"):
    for espejo in (False, True):
        for f_hz in (1.5, 2.0, 3.0):
            for pp in (0.6, 0.9):
                fotos = [esqueleto_saludo(i / FPS_S, lado=lado, f=f_hz, pp=pp, espejo=espejo,
                                          ruido=0.03, rng=rng) for i in range(int(2.5 * FPS_S))]
                med = correr_saludo(fotos)
                prim = next((i for i, m in enumerate(med) if m.detectado), None)
                check(prim is not None and med[prim].lado == lado,
                      f"saludo {lado} espejo={espejo} {f_hz} Hz pp {pp}: no detectado")
                if prim is not None:
                    t_det.append(prim / FPS_S)
check(max(t_det) <= 1.6, f"saludo: tarda {max(t_det):.2f} s en detectarse")


def episodios(med):
    return sum(1 for a, b in zip([False] + [m.detectado for m in med], [m.detectado for m in med])
               if b and not a)


# Mano levantada QUIETA (200 x 3 s): con el temblor de MediaPipe, nunca; con
# atipicos SUELTOS (al menos 2 fotogramas normales entre dos), nunca: la
# mediana de 3 los quita; con atipicos al azar en el 5 % de los fotogramas
# (pueden caer seguidos o a uno de distancia), como mucho 1 %.
falsos = {"temblor": 0, "sueltos": 0, "azar": 0}
for caso in falsos:
    for k in range(200):
        rr = np.random.default_rng(100 + k)
        fotos, ult = [], -99
        for i in range(int(3 * FPS_S)):
            p = esqueleto_saludo(i / FPS_S, pp=0.0, ruido=0.04, rng=rr, espejo=bool(k % 2))
            atip = caso != "temblor" and rr.random() < 0.05 and not (caso == "sueltos" and i - ult < 3)
            if atip:
                p[P.MUNECA_I, 0] += rr.choice([-1, 1]) * 0.6 * 0.06
                ult = i
            fotos.append(p)
        falsos[caso] += episodios(correr_saludo(fotos))
check(falsos["temblor"] == 0 and falsos["sueltos"] == 0 and falsos["azar"] <= 2,
      f"saludo: mano quieta levantada detectada {falsos}")

# Mano bajando (y subiendo y bajando, en vertical): nunca.
for rep in range(3):
    fotos = []
    for i in range(int(3 * FPS_S)):
        arriba = (i // (FPS_S // 2 + rep)) % 2 == 0      # sube y baja cada ~0.5 s
        fotos.append(esqueleto_saludo(i / FPS_S, pp=0.0, alzado=arriba, ruido=0.03, rng=rng))
    check(not any(m.detectado for m in correr_saludo(fotos)), f"saludo: mano sube y baja ({rep})")
fotos = [esqueleto_saludo(i / FPS_S, f=2.0, pp=0.6) for i in range(int(0.6 * FPS_S))]
fotos += [esqueleto_saludo(0, alzado=False) for _ in range(int(2 * FPS_S))]
med = correr_saludo(fotos)
check(not any(m.detectado for m in med[int(0.6 * FPS_S) + 6:]), "saludo: sigue tras bajar la mano")

# Oscilacion pequeña (rascarse, gesticular): no. Dos manos saludando: no.
fotos = [esqueleto_saludo(i / FPS_S, f=2.0, pp=0.2, ruido=0.02, rng=rng) for i in range(45)]
check(not any(m.detectado for m in correr_saludo(fotos)), "saludo: amplitud 0.2 detectada")
fotos = [esqueleto_saludo(i / FPS_S, f=2.0, pp=0.6, dos=True) for i in range(45)]
check(not any(m.detectado for m in correr_saludo(fotos)), "saludo: dos manos cuenta como saludo")
# De perfil (ancho de hombros ~0): la escala sale del torso y el temblor no se dispara.
fotos = [esqueleto_saludo(i / FPS_S, pp=0.0, perfil=True, ruido=0.04, rng=rng) for i in range(45)]
med = correr_saludo(fotos)
check(not any(m.detectado for m in med) and max(m.amplitud for m in med) < 0.3,
      f"saludo: de perfil se dispara (amplitud {max(m.amplitud for m in med):.2f})")
# Sin persona algunos fotogramas: se borra la historia.
fotos = [esqueleto_saludo(i / FPS_S, f=2.0, pp=0.6) for i in range(10)] + [None] * 10
med = correr_saludo(fotos)
check(med[-1].cambios == 0 and not med[-1].detectado, "saludo: sin persona no se borra")
# activo: false -> mide pero nunca detecta.
g_off = copy.deepcopy(G)
g_off["saludo"]["activo"] = False
fotos = [esqueleto_saludo(i / FPS_S, f=2.0, pp=0.6) for i in range(45)]
med = correr_saludo(fotos, cfg=g_off)
check(not any(m.detectado for m in med) and max(m.cambios for m in med) >= 3,
      "saludo: activo false detecta o no mide")
# Caso del dry-run del 2026-11-24 (experiments/.../2026-11-24T1421*..1424*):
# saludo LENTO (~0.6 Hz) y AMPLIO (pico a pico ~1.1) con la mano por encima de
# la cabeza; en el extremo del vaiven hacia dentro, durante ~5 fotogramas, la
# muñeca NO queda por encima del codo (codo subido, o codo sin ver) aunque
# siga por encima de la nariz ("mano"). Antes: el saludo perdia el brazo
# justo en el cambio de direccion, se borraba la historia y no pasaba de 1-2
# cambios.
def saludo_lento(t, variante, a=0.06, f=0.6, pp=1.1, rng=None):
    p = esqueleto("neutro", a=a, torso=1.4)
    hombro, codo, muneca, _ = P.LADOS["d"]
    hx, hy = float(p[hombro, 0]), float(p[hombro, 1])
    fuera = np.sign(hx - 0.5)
    fase = math.sin(2 * math.pi * f * t)
    x = hx + fuera * (0.3 * a + pp / 2 * a * fase)
    p[muneca] = (x, hy - 1.6 * a, 0.95)                 # por encima de la nariz
    p[codo] = (hx + fuera * 0.3 * a, hy - 0.5 * a, 0.95)
    if fase < -0.75:                                    # extremo hacia dentro
        if variante == "codo_alto":
            p[codo] = (x - fuera * 0.2 * a, hy - 1.5 * a, 0.95)
        else:
            p[codo, 2] = 0.1
    if rng is not None:
        p[:, :2] += rng.normal(0, 0.03 * a, size=(33, 2))
    return p


for variante in ("codo_alto", "codo_sin_ver"):
    rr = np.random.default_rng(24)
    fotos = [saludo_lento(i / FPS_S, variante, rng=rr) for i in range(int(6 * FPS_S))]
    huecos = [p for i, p in enumerate(fotos) if math.sin(2 * math.pi * 0.6 * i / FPS_S) < -0.75]
    esc = P.escala_saludo(huecos[0], GS, G["vis_min"])
    check(all(P.clasificar(p, G) == P.MANO for p in huecos),
          f"caso 2026-11-24 {variante}: el extremo no es 'mano'")
    check(not P.brazo_alzado(huecos[0], GS, G["vis_min"], "d", esc),
          f"caso 2026-11-24 {variante}: el caso no reproduce el hueco (codo)")
    check(all(P.brazo_alzado(p, GS, G["vis_min"], "d", P.escala_saludo(p, GS, G["vis_min"]), G)
              for p in huecos), f"caso 2026-11-24 {variante}: 'mano' lo ve y el saludo no")
    med = correr_saludo(fotos)
    prim = next((i for i, m in enumerate(med) if m.detectado), None)
    check(prim is not None and max(m.cambios for m in med) >= 3,
          f"caso 2026-11-24 {variante}: no detectado (cambios max {max(m.cambios for m in med)})")
    check(all(m.lado == "d" for m in med), f"caso 2026-11-24 {variante}: fotogramas sin lado")
    print(f"  caso 2026-11-24 ({variante}): saludo lento 0.6 Hz con el codo perdido en el extremo: "
          f"detectado a los {prim / FPS_S:.2f} s, cambios max {max(m.cambios for m in med)}")

# Fotograma suelto sin brazo (log 2026-11-24T144851, t=6.39): la historia se
# conserva y el fotograma informa los cambios que lleva, no 0.
fotos = [esqueleto_saludo(i / FPS_S, f=0.8, pp=0.9) for i in range(30)]
fotos[25] = esqueleto_saludo(25 / FPS_S, alzado=False)
med = correr_saludo(fotos)
check(med[24].cambios >= 1 and med[25].cambios == med[24].cambios
      and med[26].cambios >= med[24].cambios,
      f"saludo: un fotograma sin brazo lo reinicia {[m.cambios for m in med[23:28]]}")
print(f"  fotograma suelto sin brazo: cambios {[m.cambios for m in med[23:28]]} (no vuelve a 0)")

# Re-armado: tras bloquear(), aunque no lleguen fotogramas un rato (Hello) y
# la persona siga saludando, no se detecta; al bajar el brazo 0.3 s y volver a
# saludar, si.
d = P.DetectorSaludo(G)
t = 0.0
for i in range(30):
    m = d.paso(t, esqueleto_saludo(t)); t += 1 / FPS_S
check(m.detectado, "re-armado: no detecta el primer saludo")
d.bloquear()
t += 5.0                                             # Hello: 5 s sin fotogramas
sigue = []
for i in range(45):
    sigue.append(d.paso(t, esqueleto_saludo(t)).detectado); t += 1 / FPS_S
check(not any(sigue), "re-armado: el mismo saludo vuelve a saltar tras Hello")
for i in range(8):
    d.paso(t, esqueleto_saludo(t, alzado=False)); t += 1 / FPS_S
otra = []
for i in range(30):
    otra.append(d.paso(t, esqueleto_saludo(t)).detectado); t += 1 / FPS_S
check(any(otra) and not d.bloqueado, "re-armado: no vuelve a detectar tras bajar el brazo")
print(f"  saludo real (2 manos, espejo, 1.5-3 Hz) detectado en {min(t_det):.2f}-{max(t_det):.2f} s; "
      f"mano quieta 200 x 3 s: falsos {falsos}; sube/baja, baja, amplitud 0.2, dos manos y de "
      "perfil: 0 falsos; activo false: mide sin detectar; re-armado: bajar el brazo")


# ===========================================================================
print("\n== 2. filtro y distancia ==")
f = S.Filtro("mediana", 5, 1.0)
salida = [f.paso(x) for x in (0.75, 0.75, 0.02, 0.75, 0.75)]
check(salida[2] == 0.75 and salida[-1] == 0.75, f"mediana no quita el atipico: {salida}")
f = S.Filtro("mediana+ema", 5, 0.5)
salida = [f.paso(x) for x in [0.75] * 4 + [0.02] + [0.75] * 3]
check(min(salida) > 0.7, f"mediana+ema deja pasar el atipico: {min(salida):.3f}")
f = S.Filtro("ema", 5, 0.5)
salida = [f.paso(x) for x in [0.5] + [0.7] * 20]
check(abs(salida[-1] - 0.7) < 1e-3, "ema no converge")
check(f.paso(None) is None and f.valor is not None, "un hueco reinicia el filtro")
try:
    S.Filtro("raro", 5, 0.5); check(False, "filtro desconocido aceptado")
except ValueError:
    check(True, "")
D = S.Distancia(CS)


def tam_de(d: float) -> float:
    return CS["tam_objetivo"] + D.pendiente * (CS["d_objetivo_m"] - d)


check(abs(D.metros(CS["tam_objetivo"]) - 2.0) < 1e-9, "distancia(tam_objetivo) != 2")
check(abs(D.metros(CS["tam_cerca"]) - 1.3) < 1e-9, "distancia(tam_cerca) != 1.3")
check(abs(D.metros(tam_de(3.0)) - 3.0) < 1e-9, "tam_de no invierte metros")
check(abs(D.tam(1.7) - tam_de(1.7)) < 1e-12, "Distancia.tam no invierte metros")
# Saturacion: mas cerca de 1.3 m tam se queda en ~0.89. Los umbrales tienen
# que caer en la parte que SI se mide y con margen.
D_ATRAS = CS["marcha_atras"]["d_m"]
check(D.tam_sat - D.tam(D_ATRAS) >= 0.01,
      f"marcha atras a tam {D.tam(D_ATRAS):.3f}: pegada a la saturacion {D.tam_sat}")
check(D.d_min < D_ATRAS < CS["marcha_atras"]["d_rearme_m"] < CS["d_parar_m"] < CS["d_arrancar_m"],
      "orden de umbrales: saturacion < atras < rearme < parar < arrancar")
check(D.tam(CS["d_arrancar_m"]) < D.tam(CS["d_parar_m"]) < D.tam_sat - 0.05,
      "histeresis dentro de lo que se mide")
cs_tmp = S.ControlSeguimiento(CS, {"vx_range": [-0.5, 1.0], "wz_range": [-1.0, 1.0]})
for tam_real in (D.tam_sat, D.tam_sat - 0.005, D.tam(D_ATRAS) + 0.005):
    cs_tmp.detener(); cs_tmp.atras_bloqueada = False
    cs_tmp.paso(D.metros(tam_real), 0.0, 1 / 15, 0.0)
    check(cs_tmp.atras, f"tam {tam_real:.3f} ({D.metros(tam_real):.2f} m) no activa la marcha atras")
p = elegir([(10, 0, 50, 300, 0.9), (300, 100, 330, 200, 0.8)], 640, 360)
check(p.visible and abs(p.tam - 300 / 360) < 1e-9 and p.lateral < 0, f"YOLO elige mal: {p}")
print(f"  mediana quita atipicos; tam {CS['tam_objetivo']} -> 2.0 m, {CS['tam_cerca']} -> 1.3 m; "
      f"pendiente {D.pendiente:.3f}/m; YOLO elige la caja mayor")
print(f"  saturacion tam {D.tam_sat} = {D.d_min:.2f} m; marcha atras < {D_ATRAS} m "
      f"(tam {D.tam(D_ATRAS):.3f}); rearme {CS['marcha_atras']['d_rearme_m']} m "
      f"(tam {D.tam(CS['marcha_atras']['d_rearme_m']):.3f}); parar {CS['d_parar_m']} m "
      f"(tam {D.tam(CS['d_parar_m']):.3f}); arrancar {CS['d_arrancar_m']} m "
      f"(tam {D.tam(CS['d_arrancar_m']):.3f})")


# ===========================================================================
print("\n== 3. control ==")
LIM = {"vx_range": [-0.5, 1.0], "wz_range": [-1.0, 1.0]}


def simular(ds, lats=None, dt=1 / 15):
    c = S.ControlSeguimiento(CS, LIM)
    out = []
    for k, d in enumerate(ds):
        lat = 0.0 if lats is None else lats[k]
        out.append(c.paso(d, lat, dt, k * dt))
    return c, out


# Histéresis: ruido alrededor de 2.3 m (entre parar 2.15 y arrancar 2.5): nunca arranca.
ruido = list(2.3 + rng.uniform(-0.15, 0.15, 300))
c, out = simular(ruido)
check(all(v[0] == 0 for v in out), "arranca con ruido dentro de la histeresis")
# Arranca a 2.8, sigue en marcha con ruido a 2.3 (> 2.15), para a 2.0.
ds = [2.8] * 45 + list(2.3 + rng.uniform(-0.1, 0.1, 60)) + [2.0] * 45
c, out = simular(ds)
vx = [0.0] + [v[0] for v in out]               # parte de parado
arr = sum(1 for a, b in zip(vx, vx[1:]) if a == 0 and b > 0)
par = sum(1 for a, b in zip(vx, vx[1:]) if a > 0 and b == 0)
check(arr == 1 and par == 1, f"histeresis: arranques {arr}, paradas {par}")
check(min(v for v in vx[46:106]) >= CS["vx_marcha_min"] - 1e-9, "en marcha baja del minimo")
# Marcha suave: nunca salta mas que la rampa (al acelerar ni al frenar).
dvmax = CS["acel_vx"] / 15 + 1e-9
check(max(abs(b - a) for a, b in zip(vx, vx[1:])) <= dvmax, "salto de vx mayor que la rampa")
check(vx[1] <= dvmax, f"arranque brusco: {vx[1]}")
vx = vx[1:]
frenada = [v for v in vx[105:] if v > 0]
check(len(frenada) >= 3, f"frena sin rampa ({len(frenada)} pasos)")
c.detener()
check(c.vx == 0 and c.wz == 0 and not c.en_marcha, "detener no es inmediato")
# Velocidad: 0.5 m de mas -> 0.25; 1 m de mas -> 0.4 (tope).
c, out = simular([2.5 + 1e-3] * 60)
check(abs(out[-1][0] - 0.25) < 1e-3, f"0.5 m de mas: {out[-1][0]}")
c, out = simular([3.0] * 60)
check(abs(out[-1][0] - 0.4) < 1e-6, f"1 m de mas: {out[-1][0]}")
# Marcha atras limitada.
ds = [1.1] * int(5 * 15) + [1.8] * 15 + [1.1] * 15
c, out = simular(ds)
vx = [v[0] for v in out]
neg = [k for k, v in enumerate(vx) if v < 0]
check(min(vx) >= -CS["marcha_atras"]["vx"] - 1e-9 and min(vx) < -0.1, f"marcha atras: min {min(vx)}")
primer = [k for k in neg if k < 75]
dur_atras = (primer[-1] - primer[0] + 1) / 15
check(dur_atras <= CS["marcha_atras"]["max_s"] + 0.5, f"retrocede {dur_atras:.1f} s")
check(all(v == 0 for v in vx[int(4.0 * 15):75]), "sigue retrocediendo tras max_s")
check(any(k >= 90 for k in neg), "no vuelve a retroceder tras pasar de 1.6 m")
# Giro con histeresis.
lats = list(0.15 + rng.uniform(-0.04, 0.04, 100))          # < 0.20: no gira
c, out = simular([2.0] * 100, lats)
check(all(v[2] == 0 for v in out), "gira con lateral dentro de la histeresis")
lats = [0.4] * 30 + list(0.12 + rng.uniform(-0.03, 0.03, 60)) + [0.0] * 30
c, out = simular([2.0] * 120, lats)
wz = [0.0] + [v[2] for v in out]
on = sum(1 for a, b in zip(wz, wz[1:]) if a == 0 and b != 0)
check(on == 1 and min(wz) < 0, f"giro: encendidos {on}, min {min(wz)}")
check(max(abs(b - a) for a, b in zip(wz, wz[1:])) <= CS["acel_wz"] / 15 + 1e-9, "giro sin rampa")
check(all(v[1] == 0.0 for v in out), "vy distinto de 0")
print(f"  histeresis 2.15/2.5 m sin rebotes | vx en marcha >= {CS['vx_marcha_min']} | rampa "
      f"<= {dvmax:.3f}/paso al acelerar y frenar | marcha atras {dur_atras:.1f} s y rearme | giro "
      "con histeresis y rampa | vy = 0")


# ===========================================================================
print("\n== 4. inclinacion (unidad) ==")


MAX = abs(float(CI["max_rad"]))
SIGNO = 1.0 if float(CI["signo"]) >= 0 else -1.0
PERIODO = float(CI["periodo_envio_s"])
T_END = float(CI["t_enderezar_s"])
check(ACTIVA_YAML is True, "yaml: inclinacion.activa deberia estar en true")
check(abs(MAX - 0.25) < 1e-9, f"max_rad {MAX} (pedido 0.25)")
check(SIGNO < 0, "signo: pitch negativo levanta el morro (con +0.25 bajo, 2026-11-20)")


def inc(cfg_extra=None, codigo=0):
    env = []

    def enviar(p):
        env.append((T[0], p))
        return codigo

    return I.Inclinacion(dict(CI, **(cfg_extra or {})), enviar), env


T = [0.0]


def correr(inc_, seg, dt=0.02):
    fin = T[0] + seg
    while T[0] < fin - 1e-9:
        T[0] += dt
        inc_.paso(T[0])


# Inclinarse: Euler(signo*max) al momento y reenviado cada periodo; inclinado a t_inclinar_s.
T[0] = 0.0
inc_, env = inc()
inc_.inclinar(T[0])
check(env == [(0.0, SIGNO * MAX)], f"no envia al momento: {env}")
correr(inc_, 0.4)
check(inc_.estado == I.INCLINANDO, "inclinado antes de t_inclinar_s")
correr(inc_, 0.2)
check(inc_.estado == I.INCLINADO, "no pasa a INCLINADO tras t_inclinar_s")
correr(inc_, 2.4)                              # 3 s en total
huecos = [b[0] - a[0] for a, b in zip(env, env[1:])]
check(all(p == SIGNO * MAX for _, p in env), "pitch distinto del maximo con signo")
check(max(huecos) <= PERIODO + 0.02 + 1e-9 and 28 <= len(env) <= 32,
      f"no reenvia cada {PERIODO} s: {len(env)} envios en 3 s, hueco max {max(huecos):.3f}")
check(not inc_.recto_y_estable(), "recto estando inclinado")
# Enderezar: Euler 0 al momento, cada periodo, recto solo tras t_enderezar_s.
n0, t_e = len(env), T[0]
inc_.enderezar(T[0])
check(env[n0] == (t_e, 0.0), "no manda Euler 0 al momento")
correr(inc_, T_END - 0.06)
check(not inc_.recto_y_estable(), "recto antes de t_enderezar_s")
correr(inc_, 0.1)
ceros = env[n0:]
check(inc_.recto_y_estable() and all(p == 0.0 for _, p in ceros) and len(ceros) >= 5,
      f"enderezar: {len(ceros)} Euler 0, recto={inc_.recto_y_estable()}")
check(ceros[-1][0] - t_e >= T_END - 1e-9, "deja de mandar Euler 0 antes de tiempo")
n1 = len(env)
correr(inc_, 1.0)
check(len(env) == n1, "sigue mandando Euler estando recto")
# Desactivada: no hace nada.
inc_, env = inc({"activa": False})
inc_.inclinar(0.0); correr(inc_, 1.0)
check(not env and inc_.recto_y_estable(), "inclina desactivada")
# Lista blanca que rechaza: se desactiva y endereza, no rompe.
env2 = []


def rechaza(p):
    env2.append(p)
    if p != 0.0:
        raise ValueError("postura")
    return 0


T[0] = 0.0
inc_ = I.Inclinacion(CI, rechaza)
inc_.inclinar(0.0); correr(inc_, 1.0)
check(not inc_.activa and inc_.recto_y_estable() and env2[-1] == 0.0,
      "un rechazo de la lista blanca no desactiva / no endereza")
# Código distinto de 0: se desactiva.
T[0] = 0.0
inc_, env = inc(codigo=3104)
with redirect_stdout(io.StringIO()):
    inc_.inclinar(0.0); correr(inc_, 1.0)
check(not inc_.activa and env[-1][1] == 0.0, "codigo != 0 no desactiva")
print(f"  Euler(0, {SIGNO * MAX:+.2f}, 0) al momento y cada {PERIODO} s; inclinado a los "
      f"{CI['t_inclinar_s']} s | Euler 0 cada {PERIODO} s y recto a los {T_END} s | "
      "sin IMU | rechazo o codigo != 0 -> se desactiva")


# ===========================================================================
print("\n== 5. confirmador ==")
sys.modules.setdefault("unitree_sdk2py.go2.sport.sport_client", types.ModuleType("x"))


REG: list[tuple[float, str, tuple]] = []
N_SPORT = {"n": 0}


class SportFalso:
    altura = 0.07
    rechazar = False
    pitch = 0.0
    imu_muerta = False

    def __init__(self):
        N_SPORT["n"] += 1

    def SetTimeout(self, t): pass
    def Init(self): pass

    def _r(self, nombre, *a):
        REG.append((time.monotonic(), nombre, a))

    def _set(self, h):
        SportFalso.altura = h

    def StandUp(self): self._r("StandUp"); self._set(0.31); return 0
    def BalanceStand(self): self._r("BalanceStand"); return 0
    def StopMove(self): self._r("StopMove"); return 0
    def StandDown(self): self._r("StandDown"); self._set(0.07); return 0
    def RiseSit(self):
        self._r("RiseSit")
        if not SportFalso.rechazar:
            self._set(0.31)
        return 0
    def Hello(self):
        self._r("Hello"); self._set(0.275)
        threading.Timer(0.2, self._set, (0.31,)).start()
        return 0
    def Move(self, vx, vy, wz): self._r("Move", vx, vy, wz); return 0
    euler_codigo = 0         # 3203 = el robot no implementa Euler
    # Modelo de la IMU de rt/lowstate para --probar-inclinacion (apagado por
    # defecto: entonces leer_pitch_low da None). El cuerpo sigue al ultimo
    # Euler con `retardo_s`; los Euler antes de `ignorar_hasta` (monotonic) se
    # aceptan con codigo 0 pero no hacen nada (como la 1a prueba en el robot).
    modelo_low = False
    retardo_s = 0.0
    ignorar_hasta = 0.0
    hist_euler: list = []

    def Euler(self, r, p, y):
        self._r("Euler", r, p, y)
        if SportFalso.euler_codigo:
            return SportFalso.euler_codigo
        if time.monotonic() >= SportFalso.ignorar_hasta:
            SportFalso.hist_euler.append((time.monotonic(), p))
        if not SportFalso.imu_muerta:
            SportFalso.pitch = p
        return 0
    def Sit(self): self._r("Sit"); return 0
    def GetApiVersion(self): return "1.0.0.1"
    def GetServerApiVersion(self): return 0, "1.0.0.1"


class MotionSwitcherFalso:
    def CheckMode(self):
        return 0, {"form": "0", "name": "normal"}


mod = types.ModuleType("unitree_sdk2py.go2.sport.sport_client")
mod.SportClient = SportFalso
sys.modules["unitree_sdk2py.go2.sport.sport_client"] = mod

import gesture_follow as GF  # noqa: E402
GF.crear_motion_switcher = MotionSwitcherFalso

conf = GF.Confirmador({"dos_manos": 3, "mano": 3, "cruzados": 3, "t": 3}, True)
r = [conf.paso(P.DOS, True) for _ in range(3)]
check(r[-1] == P.DOS, "no confirma")
conf.consumido(P.DOS)
r = [conf.paso(P.DOS, True) for _ in range(10)]
check(all(x is None for x in r), "el mismo gesto mantenido rebota")
r = [conf.paso(P.T, True) for _ in range(3)]
check(r[-1] == P.T, "otro gesto necesita bajar los brazos")
conf.consumido(P.T)
conf.paso(P.NINGUNO, True)
r = [conf.paso(P.T, True) for _ in range(3)]
check(r[-1] == P.T, "tras dejar de verlo no vuelve a contar")
# Saludo: sin N fotogramas, con re-armado y roto por una imagen vieja.
conf = GF.Confirmador({"dos_manos": 3, "mano": 3, "cruzados": 3, "t": 3}, True)
check(conf.paso(P.SALUDO, True) == P.SALUDO, "saludo: pide fotogramas de mas")
check(conf.paso(P.SALUDO, False) is None, "saludo: confirma con imagen vieja")
conf.consumido(P.SALUDO)
check(all(conf.paso(P.SALUDO, True) is None for _ in range(20)), "saludo mantenido rebota")
conf.paso(P.NINGUNO, True)
check(conf.paso(P.SALUDO, True) == P.SALUDO, "saludo: no se re-arma al dejar de verse")
r = [conf.paso(P.MANO, True) for _ in range(2)] + [conf.paso(P.SALUDO, True)]
check(r == [None, None, P.SALUDO], f"saludo tras mano: {r}")
print("  el gesto que actua queda bloqueado hasta dejar de verse; otro gesto cuenta directamente; "
      "saludo sin fotogramas extra, con re-armado")


class EstadoFalso:
    def __init__(self, cfg):
        self.cfg, self.n = cfg, 1
        self.congelar_en: float | None = None
        self.descongelar_en: float | None = None
        self.sin_datos = False

    def leer(self):
        if self.sin_datos:
            return None, None, float("inf")
        ahora = time.monotonic()
        if (self.congelar_en is not None and ahora >= self.congelar_en
                and (self.descongelar_en is None or ahora < self.descongelar_en)):
            return 0, SportFalso.altura, ahora - self.congelar_en
        return 0, SportFalso.altura, 0.01

    def leer_pitch(self):
        m, h, e = self.leer()
        return None if e > 0.5 else SportFalso.pitch

    def leer_pitch_low(self):
        if not SportFalso.modelo_low:
            return None
        limite = time.monotonic() - SportFalso.retardo_s
        previos = [p for t, p in SportFalso.hist_euler if t <= limite]
        return previos[-1] if previos else 0.0      # rad; mismo signo que la orden

    def estado(self):
        m, h, e = self.leer()
        if h is None or e > 0.5:
            return GF.SIN_DATOS
        return GF.clasificar_postura(m, h, self.cfg)

    def esperar_primero(self, t):
        return True


ACC = dict(CFG["acciones"], espera_standup_s=0.05, espera_balance_s=0.05,
           espera_stopmove_s=0.05, espera_hello_s=0.3, estable_s=0.1,
           timeout_estable_s=1.5, timeout_alto_s=0.5, timeout_standdown_s=0.5)
CE = CFG["estado_robot"]
LIM_MOVE = {"vx_max": 0.4, "wz_max": 0.6, "vx_atras": 0.15, "pitch_max_rad": 0.25}


# ===========================================================================
print("\n== 6. Robot ==")


def nuevo_robot(altura, modo=GF.QUIETO):
    SportFalso.altura, SportFalso.rechazar, SportFalso.pitch = altura, False, 0.0
    SportFalso.imu_muerta = False
    REG.clear()
    GF.PARAR["flag"] = False
    est = EstadoFalso(CE)
    with redirect_stdout(io.StringIO()):
        rb = GF.Robot(GF.SalidaRobot(LIM_MOVE, 2.0), est, ACC, CE, CI, dry_run=False)
    rb.modo = modo
    return rb, est


alturas = {"TUMBADO": 0.07, "SENTADO": 0.235, "DE_PIE": 0.31, "DUDOSO": 0.275}
esperado = {
    (P.MANO, "TUMBADO", "QUIETO"): ("levantar", ["StandUp", "BalanceStand"]),
    (P.DOS, "DE_PIE", "QUIETO"): ("seguir", []),
    (P.T, "DE_PIE", "QUIETO"): ("saludar", ["Hello", "BalanceStand"]),
    (P.SALUDO, "DE_PIE", "QUIETO"): ("saludar", ["Hello", "BalanceStand"]),
    (P.SALUDO, "TUMBADO", "QUIETO"): ("levantar", ["StandUp", "BalanceStand"]),
    (P.CRUZADOS, "DE_PIE", "QUIETO"): ("tumbar", ["StopMove", "StandDown"]),
    (P.DOS, "DE_PIE", "SIGUIENDO"): ("parar", ["Move", "StopMove", "BalanceStand"]),
    (P.T, "DE_PIE", "SIGUIENDO"): ("parar", ["Move", "StopMove", "BalanceStand"]),
}
for modo in ("QUIETO", "SIGUIENDO"):
    for gesto in (P.MANO, P.DOS, P.T, P.CRUZADOS, P.SALUDO):
        for est, h in alturas.items():
            if modo != "QUIETO" and est != "DE_PIE":
                continue
            rb, _ = nuevo_robot(h, modo)
            a, motivo = GF.decidir(gesto, rb.postura(), rb.modo)
            ll = []
            if a != "nada":
                with redirect_stdout(io.StringIO()):
                    res = GF.ejecutar(a, rb)
                ll = [x[1] for x in REG]
                check(res == "ok", f"{gesto}x{est}x{modo}: {res}")
            exp = esperado.get((gesto, est, modo), ("nada", []))
            check((a, ll) == exp, f"{gesto} x {est} x {modo}: {a} {ll} (esperado {exp})")
# Rango de pie activo: 0.27 quieto es SENTADO; siguiendo es DE_PIE.
rb, _ = nuevo_robot(0.27)
check(rb.postura() == GF.SENTADO, f"0.27 quieto: {rb.postura()}")
rb.modo = GF.SIGUIENDO
check(rb.postura() == GF.DE_PIE, f"0.27 siguiendo: {rb.postura()}")
# Lista blanca.
rb, _ = nuevo_robot(0.31, GF.SIGUIENDO)
for vx, vy, wz in ((0.1, 0.1, 0.0), (-0.2, 0.0, 0.0), (0.5, 0.0, 0.0), (0.1, 0.0, 0.7)):
    for sal in (rb.salida, GF.SalidaNula(LIM_MOVE)):
        try:
            sal.mover(vx, vy, wz); check(False, f"Move({vx},{vy},{wz}) permitido")
        except ValueError:
            check(True, "")
for ro, pi, ya in ((0.1, 0.0, 0.0), (0.0, math.radians(16), 0.0), (0.0, 0.0, 0.1)):
    for sal in (rb.salida, GF.SalidaNula(LIM_MOVE)):
        try:
            sal.euler(ro, pi, ya); check(False, f"Euler({ro},{pi},{ya}) permitido")
        except ValueError:
            check(True, "")
for sal in (rb.salida, GF.SalidaNula(LIM_MOVE)):
    for prohibida in ("Sit", "Move", "Euler", "FrontFlip"):
        try:
            sal.llamar(prohibida); check(False, f"{prohibida} por nombre permitida")
        except ValueError:
            check(True, "")
rb, _ = nuevo_robot(0.31, GF.QUIETO)
try:
    rb.mover(0.2, 0.0); check(False, "Move en QUIETO")
except ValueError:
    check(True, "")
try:
    rb._enviar_euler(0.1); check(False, "Euler sin BalanceStand")
except ValueError:
    check(True, "")
rb.modo = GF.SIGUIENDO
rb.incl.estado = I.INCLINADO
try:
    rb.mover(0.2, 0.0); check(False, "Move inclinado")
except ValueError:
    check(True, "")
print("  tabla gesto x estado x modo; 0.27 m: SENTADO quieto, DE_PIE siguiendo; Move con vy, "
      "atras > 0.15, > topes, en QUIETO o inclinado: rechazado; Euler con roll/yaw, > 0.25 rad "
      "o sin BalanceStand: rechazado; Sit/Move/Euler por nombre: rechazado")


# ===========================================================================
print("\n== 7. main() por el camino real con robot falso ==")
FPS = 15.0


class CamaraFalsa:
    def __init__(self, n, congelar_desde=None):
        self.n, self.t0, self.congelar_desde = n, time.monotonic(), congelar_desde
        self.i_actual, self.actual = -1, None

    def leer(self):
        i = int((time.monotonic() - self.t0) * FPS)
        if self.congelar_desde is not None:
            i = min(i, self.congelar_desde)
        if i != self.i_actual:
            self.i_actual = i
            self.actual = np.random.default_rng(i).integers(0, 255, (360, 640, 3), dtype=np.uint8)
        return self.actual


def grados_arriba() -> float:
    return abs(math.degrees(SportFalso.pitch))


def guion_a_frames(guion):
    """guion: (gesto|None|"auto", segundos, d|None, lat). gesto None: MediaPipe
    no ve a nadie. "auto": nariz más baja en la imagen cuanto más inclinado
    (a 8 grados queda en el margen). d None: YOLO no ve a nadie."""
    out = []
    for g, seg, d, lat in guion:
        out += [(g, d, lat)] * int(round(seg * FPS))
    return out


def ejecutar_main(guion, altura=0.31, congelar_desde=None, estado_cfg=None,
                  cfg_extra=None, duracion=None, extra=(), durante=None):
    frames = guion_a_frames(guion)
    cam = CamaraFalsa(len(frames), congelar_desde)

    def actual():
        return frames[min(cam.i_actual, len(frames) - 1)]

    class DetectorFalso(P.DetectorGestos):
        def __init__(self, cfg, **kw):
            self.cfg, self.cfg_gestos = cfg, cfg["gestos"]
            self.vis_min = float(cfg["gestos"]["vis_min"])
            self.ancho_salida = 640
            self.cam, self.cap = cam, None
            self.fps, self._firma_prev, self.motivo_sin_imagen = 15.0, None, ""

        def detectar(self, imagen):
            g, d, lat = actual()
            if g is None:
                return P.Pose(visible=False)
            if g == "auto":
                nariz = 0.02 + 0.01 * grados_arriba()
                p = esqueleto("neutro", hy=nariz + 0.045)
            elif g in ("saludo", "saludo_d"):
                # Fase por el número de fotograma: oscila a 2 Hz.
                p = esqueleto_saludo(cam.i_actual / FPS, lado="d" if g == "saludo_d" else "i")
            else:
                p = esqueleto(g)
            return P.pose_desde_puntos(p, self.cfg_gestos, imagen.shape)

        def cerrar(self):
            pass

    class YoloFalso:
        def detectar(self, imagen):
            g, d, lat = actual()
            if d is None:
                return Persona(visible=False)
            # Como la camara real: mas cerca de ~1.3 m tam satura (caja cortada).
            return Persona(visible=True, lateral=lat, tam=min(tam_de(d), D.tam_sat), ms=5.0)

    P.DetectorGestos = DetectorFalso
    GF.crear_personas = lambda cfg: YoloFalso()
    SportFalso.altura, SportFalso.rechazar = altura, False
    SportFalso.pitch, SportFalso.imu_muerta = 0.0, False
    REG.clear()
    N_SPORT["n"] = 0
    est = EstadoFalso(CE)
    if estado_cfg:
        estado_cfg(est, cam.t0)
    GF.crear_estado = lambda *a, **k: est
    cfg = copy.deepcopy(CFG)
    cfg["acciones"].update(cooldown_s=1.0, espera_standup_s=0.05, espera_balance_s=0.05,
                           espera_stopmove_s=0.05, espera_hello_s=0.2, estable_s=0.05)
    for k, v in (cfg_extra or {}).items():
        cfg[k].update(v)
    ruta = TMP / "cfg.yaml"
    ruta.write_text(yaml.safe_dump(cfg))
    dur = duracion or (len(frames) / FPS)
    sys.argv = ["gesture_follow.py", "--source", "webcam", "--config", str(ruta),
                "--duration", str(dur), *extra]
    GF.PARAR["flag"] = False
    if durante:
        durante(cam.t0)
    entrada = builtins.input
    builtins.input = lambda *_: "si"
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            codigo = GF.main()
    finally:
        builtins.input = entrada
    t0 = cam.t0
    reg = [(t - t0, n, a) for t, n, a in REG]
    # --probar-inclinacion elige su propia espera tras BalanceStand.
    comprobar_invariantes(reg, buf.getvalue(), bucle="--probar-inclinacion" not in extra)
    return codigo, buf.getvalue(), reg


def nombres(reg):
    return [n for _, n, _ in reg if n not in ("Move", "Euler")]


def moves(reg):
    return [(t, a) for t, n, a in reg if n == "Move"]


def eulers(reg):
    return [(t, abs(math.degrees(a[1]))) for t, n, a in reg if n == "Euler"]


ESPERA_BAL = float(CI["espera_balance_s"])


def comprobar_invariantes(reg, out, bucle=True):
    """En TODAS las ejecuciones: límites de Move y Euler, nunca Sit, nunca
    Move con el cuerpo inclinado ni antes de t_enderezar_s tras Euler 0, y
    Euler reenviado cada ~0.1 s mientras está inclinado o enderezando. En el
    bucle principal (bucle=True), además: ningún Euler inclinado antes de
    espera_balance_s tras el último BalanceStand."""
    ultimo_euler, t_euler0, t_ult = 0.0, -1e9, None
    t_bal = -1e9
    for t, n, a in reg:
        if n == "BalanceStand":
            t_bal = t
        if bucle and n == "Euler" and a[1] != 0.0 and ultimo_euler == 0.0:
            check(t - t_bal >= ESPERA_BAL - 0.02,
                  f"Euler inclinado {t - t_bal:.2f} s despues de BalanceStand t={t:.2f}")
        if n == "Move":
            vx, vy, wz = a
            check(vy == 0.0 and -0.15 - 1e-9 <= vx <= 0.4 + 1e-9 and abs(wz) <= 0.6 + 1e-9,
                  f"Move fuera de limites {a} t={t:.2f}")
            check(ultimo_euler == 0.0, f"Move con el cuerpo inclinado t={t:.2f}")
            check(t - t_euler0 >= T_END - 0.02,
                  f"Move {t - t_euler0:.2f} s despues de Euler 0 (sin volver a recto)")
        elif n == "Euler":
            check(a[0] == 0.0 and a[2] == 0.0 and abs(a[1]) <= MAX + 1e-9,
                  f"Euler fuera de limites {a}")
            check(a[1] == 0.0 or a[1] == SIGNO * MAX, f"Euler con otro pitch {a}")
            # Mientras está inclinado o enderezando, ningún hueco > periodo + margen.
            if t_ult is not None and (ultimo_euler != 0.0 or t - t_euler0 < T_END):
                check(t - t_ult <= PERIODO + 0.08,
                      f"Euler no reenviado: hueco {t - t_ult:.2f} s en t={t:.2f}")
            if a[1] == 0.0 and ultimo_euler != 0.0:
                t_euler0 = t
            ultimo_euler, t_ult = a[1], t
    check("Sit" not in nombres(reg), "Sit")
    check("[ERROR]" not in out, f"error en main: {out[out.find('[ERROR]'):][:200]}")


LEJOS, CERCA = 3.0, 1.1

# --- A. interruptor sin rebote ---------------------------------------------
guion = [("neutro", 0.5, LEJOS, 0.0), ("dos_manos", 4.0, LEJOS, 0.0),
         ("neutro", 1.0, LEJOS, 0.0), ("dos_manos", 1.3, LEJOS, 0.0), ("neutro", 1.0, LEJOS, 0.0)]
codigo, out, reg = ejecutar_main(guion)
check(out.count("-> seguir") == 1 and out.count("-> parar") == 1,
      f"A: seguir {out.count('-> seguir')}, parar {out.count('-> parar')}")
mv = moves(reg)
check(mv and max(a[0] for _, a in mv) >= 0.25, "A: no avanza")
print(f"  A. dos manos 4 s: seguir 1 vez, parar al repetir tras bajar los brazos; "
      f"vx max {max(a[0] for _, a in mv):.2f}")

# --- B. parar NO espera al cooldown -----------------------------------------
guion = [("dos_manos", 1.3, 2.0, 0.0), ("neutro", 0.4, 2.0, 0.0),
         ("t", 1.3, 2.0, 0.0), ("neutro", 0.5, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion, cfg_extra={"acciones": {"cooldown_s": 30.0}})
check(out.count("-> parar") == 1, "B: la T no para durante el cooldown")
print("  B. con cooldown 30 s la T para igualmente")

# --- C. gesto pendiente durante el cooldown ---------------------------------
# Tumbado: una mano (levanta) y, SIN bajar los brazos, dos manos: se confirma
# durante el cooldown, queda pendiente y se ejecuta al acabar.
guion = [("mano_i", 1.3, 2.0, 0.0), ("dos_manos", 3.5, 2.0, 0.0), ("neutro", 0.5, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion, altura=0.07,
                                 cfg_extra={"acciones": {"cooldown_s": 2.0}})
t_lev = next(t for t, n, _ in reg if n == "BalanceStand")
check("pendiente" in out and out.count("-> seguir") == 1, "C: no queda pendiente o no se ejecuta")
lineas = out.splitlines()
i_seg = next(i for i, l in enumerate(lineas) if "-> seguir" in l)
t_seg = float(lineas[i_seg].split("[")[1].split("s]")[0])
check(t_seg >= t_lev + 2.0 - 0.15, f"C: seguir antes de acabar el cooldown ({t_seg:.2f} vs {t_lev + 2:.2f})")
check(out.count("-> parar") == 0, "C: dos manos mantenido rebota tras seguir")
print(f"  C. dos manos durante el cooldown: pendiente y se ejecuta a los {t_seg - t_lev:.1f} s "
      "de levantarse, sin bajar los brazos; mantenido no rebota")

# --- D. pérdida de la persona; cabeza fuera NO es pérdida ----------------------
guion = [("dos_manos", 1.3, LEJOS, 0.0), (None, 2.5, LEJOS, 0.0),     # sin cabeza: sigue
         (None, 1.5, None, 0.0), (None, 2.0, LEJOS, 0.0), (None, 7.0, None, 0.0)]
codigo, out, reg = ejecutar_main(guion)
mv = moves(reg)
sin_cabeza = [a for t, a in mv if 1.6 < t < 3.8]
check(sin_cabeza and max(a[0] for a in sin_cabeza) > 0 and "PAUSADO" not in out.split("persona perdida")[0],
      "D: sin cabeza lo trata como perdida")
t_p1 = 3.8
cero = next((t for t, a in mv if t > t_p1 and a == (0.0, 0.0, 0.0)), None)
check(cero is not None and cero - t_p1 <= 0.5 + 0.2, f"D: velocidad 0 tarde: {cero}")
check("persona recuperada" in out, "D: no recupera")
t_p2 = 3.8 + 1.5 + 2.0
t_stop = next((t for t, n, _ in reg if n == "StopMove" and t > t_p2), None)
check(t_stop is not None and 4.8 <= t_stop - t_p2 <= 5.8, f"D: deja de seguir a {t_stop}")
print(f"  D. sin cabeza (solo YOLO) sigue andando; sin persona: velocidad 0 a los "
      f"{cero - t_p1:.2f} s, recupera, y deja de seguir a los {t_stop - t_p2:.1f} s")

# --- E. cámara congelada --------------------------------------------------------
guion = [("dos_manos", 1.3, LEJOS, 0.0), ("neutro", 2.0, LEJOS, 0.0), ("dos_manos", 5.0, LEJOS, 0.0)]
congelar = int((1.3 + 2.0 + 0.3) * FPS)
codigo, out, reg = ejecutar_main(guion, congelar_desde=congelar, duracion=12.0)
t_cong = (congelar + 1) / FPS
mv = moves(reg)
cero = next((t for t, a in mv if t > t_cong and a == (0.0, 0.0, 0.0)), None)
check(cero is not None and cero - t_cong <= 0.5 + 0.15, f"E: velocidad 0 tarde: {cero}")
check(all(a == (0.0, 0.0, 0.0) for t, a in mv if cero and t >= cero), "E: Move no nulo congelada")
check(out.count("-> parar") == 0, "E: confirma un gesto con la camara congelada")
print(f"  E. camara congelada: Move(0,0,0) a los {cero - t_cong:.2f} s, ningun gesto")

# --- F. estado viejo -----------------------------------------------------------
T_EST = 1.3 + 2.0


def estado_viejo(est, t0):
    est.congelar_en = t0 + T_EST
    est.descongelar_en = t0 + T_EST + 1.5


guion = [("dos_manos", 1.3, LEJOS, 0.0), ("neutro", 2.0, LEJOS, 0.0),
         ("dos_manos", 1.5, LEJOS, 0.0), ("neutro", 2.0, LEJOS, 0.0)]
codigo, out, reg = ejecutar_main(guion, estado_cfg=estado_viejo)
mv = moves(reg)
cero = next((t for t, a in mv if t > T_EST and a == (0.0, 0.0, 0.0)), None)
check(cero is not None and cero - T_EST <= 0.5 + 0.15, f"F: velocidad 0 tarde: {cero}")
check(out.count("-> parar") == 0, "F: confirma un gesto con el estado viejo")
check(any(a[0] > 0 for t, a in mv if t > T_EST + 2.0), "F: no sigue al volver el estado")
print(f"  F. estado viejo: velocidad 0 a los {cero - T_EST:.2f} s; dos manos no cuenta")

# --- G. histéresis en el bucle completo ----------------------------------------
rr = np.random.default_rng(5)
guion = [("dos_manos", 1.3, 2.0, 0.0)]
guion += [("neutro", 1 / FPS, float(2.3 + rr.uniform(-0.12, 0.12)), 0.0) for _ in range(45)]
guion += [("neutro", 2.5, 2.9, 0.0)]
guion += [("neutro", 1 / FPS, float(2.3 + rr.uniform(-0.1, 0.1)), 0.0) for _ in range(30)]
guion += [("neutro", 1.5, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion)
mv = moves(reg)
vx = [a[0] for _, a in mv]
arr = sum(1 for a, b in zip([0.0] + vx, vx) if a == 0 and b > 0)
check(arr == 1, f"G: arranques {arr} (ruido dentro de la histeresis)")
check(all(a[0] == 0 for t, a in mv if t < 1.3 + 3.0), "G: arranca con ruido a 2.3 m")
print(f"  G. ruido +-0.12 m a 2.3 m: no arranca; a 2.9 m arranca; ruido en marcha: no para "
      f"hasta 2.0 m ({arr} arranque)")

# --- H. marcha atrás -------------------------------------------------------------
# Tras retroceder se para y se inclina; inclinado no mide la distancia: el
# paso atras a 1.8 m se nota por el cambio de tam, se endereza, mide recto
# 1.8 m (rearme) y al volver a 1.1 m retrocede otra vez.
guion = [("dos_manos", 1.3, 2.0, 0.0), ("neutro", 7.0, CERCA, 0.0), ("neutro", 1.2, 1.8, 0.0),
         ("neutro", 1.5, CERCA, 0.0)]
codigo, out, reg = ejecutar_main(guion)
mv = moves(reg)
neg = [t for t, a in mv if a[0] < 0]
check(neg and min(a[0] for _, a in mv) >= -0.15 - 1e-9, "H: no retrocede o demasiado rapido")
prim = [t for t in neg if t < 8.3]
check(prim and prim[-1] - prim[0] <= 3.0 + 0.6, f"H: retrocede {prim[-1] - prim[0]:.1f} s")
check(any(t > 9.4 for t in neg), "H: no vuelve a retroceder tras alejarse")
print(f"  H. a 1.1 m retrocede a -0.15 m/s {prim[-1] - prim[0]:.1f} s y se para; tras "
      "alejarse a 1.8 m vuelve a poder retroceder")

# --- I. inclinación siguiendo parado; recto antes de Move ----------------------
guion = [("dos_manos", 1.3, 2.0, 0.0), ("auto", 5.0, 2.0, 0.0), ("auto", 3.0, LEJOS, 0.0)]
codigo, out, reg = ejecutar_main(guion)
mv = moves(reg)
t_seg = 1.0                                       # dos manos confirmado (15 fotogramas)
eu = [(t, a[1]) for t, n, a in reg if n == "Euler" and t > t_seg + 0.6]
t_eu1 = next((t for t, p in eu if p != 0.0), None)
t_balance = max((t for t, n, _ in reg if n == "BalanceStand" and t < (t_eu1 or 0)), default=None)
check(t_balance is not None and t_eu1 is not None
      and not any(t_balance < t < t_eu1 for t, _ in mv),
      "I: no se inclina siguiendo parado, o sin BalanceStand (o con Move) antes")
# Parado >= 1 s: recto (fin del Euler 0 al empezar a seguir) y sin Move distinto de 0.
eu_todos = [(t, a[1]) for t, n, a in reg if n == "Euler"]
t_recto = max((t for t, p in eu_todos if p == 0.0 and t < (t_eu1 or 0)), default=0.0)
t_mov = max((t for t, a in mv if t < (t_eu1 or 0) and (a[0] or a[2])), default=-1e9)
check(t_eu1 is not None and t_eu1 - max(t_recto, t_mov) >= 1.0 - 0.05,
      "I: se inclina sin llevar 1 s parado")
inclinado = [t for t, p in eu if p != 0.0]
check(len(inclinado) >= 0.8 * (inclinado[-1] - inclinado[0]) / PERIODO,
      f"I: no reenvia Euler cada {PERIODO} s ({len(inclinado)} envios)")
t_e0 = next((t for t, p in eu if p == 0.0 and t > t_eu1), None)
mv_tras = [t for t, a in mv if a[0] > 0 and t > (t_e0 or 99)]
check(t_e0 is not None and mv_tras, "I: no se endereza ni avanza cuando la persona se aleja")
check("enderezo" in out, "I: no avisa del enderezado")
if not (t_e0 is not None and mv_tras):
    print("    eventos:", [(round(t, 2), n, tuple(round(x, 3) for x in a)) for t, n, a in reg][:60])
    print("    salida:", [l for l in out.splitlines() if l.strip() and "[dbg]" not in l][-25:])
    sys.exit(1)
print(f"  I. siguiendo parado >1 s en BalanceStand: Euler({SIGNO * MAX:+.2f}) {len(inclinado)} "
      f"veces; la persona se aleja: Euler 0 y avanza {mv_tras[0] - t_e0:.2f} s despues")

# --- J. quieto de pie al arrancar (sin BalanceStand): BalanceStand y se inclina -----
guion = [("neutro", 3.0, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion)
seq = [n for _, n, _ in reg]
eu = [(t, a[1]) for t, n, a in reg if n == "Euler"]
check(seq[:1] == ["BalanceStand"] and any(p == SIGNO * MAX for _, p in eu),
      f"J: quieto de pie no se inclina: {seq[:4]}")
t_bal_j = next(t for t, n, _ in reg if n == "BalanceStand")
t_inc_j = next((t for t, p in eu if p != 0.0), None)
check(t_inc_j is not None and ESPERA_BAL - 0.02 <= t_inc_j - t_bal_j <= ESPERA_BAL + 0.3,
      f"J: primer Euler {None if t_inc_j is None else round(t_inc_j - t_bal_j, 2)} s tras BalanceStand")
check(not moves(reg), "J: Move estando quieto")
check(eu[-1][1] == 0.0 and seq[-2:] == ["StopMove", "StandDown"], f"J: cierre {seq[-4:]}")
print(f"  J. quieto de pie al arrancar: BalanceStand, {t_inc_j - t_bal_j:.2f} s despues Euler({SIGNO * MAX:+.2f}) cada "
      f"{PERIODO} s ({sum(1 for _, p in eu if p)} envios), cierre con Euler 0")

# --- K. quieto inclinado y cruzados: Euler 0 antes de StandDown ----------------
guion = [("mano_i", 1.3, 2.0, 0.0), ("auto", 3.0, 2.0, 0.0), ("cruzados", 1.3, 2.0, 0.0),
         ("neutro", 0.5, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion, altura=0.07)
eu = [(t, a[1]) for t, n, a in reg if n == "Euler"]
check(any(p != 0.0 for _, p in eu), "K: no se inclina quieto")
t_sd = next((t for t, n, _ in reg if n == "StandDown"), None)
t_stop = next((t for t, n, _ in reg if n == "StopMove"), None)
antes = [(t, p) for t, p in eu if t < (t_sd or 99)]
t_ult_inc = max(t for t, p in antes if p != 0.0)
ceros = [t for t, p in antes if p == 0.0 and t > t_ult_inc]
check(t_sd and antes and antes[-1][1] == 0.0 and t_ult_inc < t_stop
      and ceros and t_stop - ceros[0] >= T_END - 0.02,
      "K: StandDown sin Euler 0 (t_enderezar_s) antes")
check(not moves(reg), "K: Move estando quieto")
print("  K. quieto inclinado y cruzados: Euler 0 durante "
      f"{t_stop - ceros[0]:.2f} s, StopMove, StandDown; 0 Move")

# --- L. cierre inclinado siguiendo -----------------------------------------------
guion = [("dos_manos", 1.3, 2.0, 0.0), ("auto", 4.0, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion)
seq = [(t, n, a) for t, n, a in reg]
i_inc = max(i for i, (t, n, a) in enumerate(seq) if n == "Euler" and a[1] != 0.0)
cola = [n for _, n, _ in seq[i_inc + 1:]]
ceros = [t for t, n, a in seq[i_inc + 1:] if n == "Euler"]
t_sd = next(t for t, n, _ in seq if n == "StandDown")
check(cola[:1] == ["Euler"] and cola[-2:] == ["StopMove", "StandDown"]
      and t_sd - ceros[0] >= T_END - 0.02, f"L: cierre {cola}")
print(f"  L. cierre inclinado siguiendo: {len(ceros)} x Euler 0 y despues "
      f"{[n for n in cola if n != 'Euler']}")

# --- M. body_height al andar 0.27 -----------------------------------------------
def bajar_altura(t0):
    threading.Timer(2.0, lambda: setattr(SportFalso, "altura", 0.27)).start()


guion = [("dos_manos", 1.3, LEJOS, 0.0), ("neutro", 3.0, LEJOS, 0.0)]
codigo, out, reg = ejecutar_main(guion, durante=bajar_altura)
check("deja de seguir" not in out and any(a[0] > 0 for t, a in moves(reg) if t > 2.5),
      "M: deja de seguir con 0.27 m andando")
print(f"  M. body_height 0.27 andando: sigue (rango de pie activo {CE['de_pie_activo'][0]}-"
      f"{CE['de_pie_activo'][1]})")
# Inclinado sube body_height (0.361-0.366 medido): sigue contando como de pie.
rb, _ = nuevo_robot(0.366, GF.QUIETO)
check(rb.postura() == GF.DUDOSO, f"0.366 recto y quieto: {rb.postura()}")
rb.incl.estado = I.INCLINADO
check(rb.postura() == GF.DE_PIE, f"0.366 inclinado: {rb.postura()} (de_pie_activo)")
print("  M. body_height 0.366 inclinado: DE_PIE (de_pie_activo hasta 0.40); recto y quieto: DUDOSO")

# --- N. la IMU no cambia (como en el robot): se inclina igual, no se desactiva ----
def imu_muerta(t0):
    SportFalso.imu_muerta = True


guion = [("dos_manos", 1.3, 2.0, 0.0), (None, 5.0, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion, durante=imu_muerta)
eu = eulers(reg)
check("inclinacion desactivada" not in out and sum(1 for _, g in eu if g > 0) >= 10,
      "N: depende de la IMU")
print("  N. la IMU no cambia nunca: se inclina igual (solo tiempos)")

# --- P. --probar-inclinacion: el mismo método, con diagnóstico y medida -----------
GF.paths.EXPERIMENTS = TMP / "experiments"


def probar(euler_codigo=0, modelo=False, retardo=0.0, ignorar_s=None, **kw):
    SportFalso.altura, SportFalso.pitch, SportFalso.imu_muerta = 0.31, 0.0, False
    SportFalso.euler_codigo = euler_codigo
    SportFalso.modelo_low, SportFalso.retardo_s, SportFalso.hist_euler = modelo, retardo, []
    SportFalso.ignorar_hasta = 0.0 if ignorar_s is None else time.monotonic() + ignorar_s
    REG.clear()
    entrada = builtins.input
    builtins.input = lambda *_: "s"
    buf = io.StringIO()
    kw.setdefault("mantener_s", 1.0)
    try:
        with redirect_stdout(buf):
            r = GF.probar_inclinacion(GF.SalidaRobot(LIM_MOVE, 2.0), EstadoFalso(CE), CI,
                                      CFG["acciones"], dry_run=False, **kw)
    finally:
        builtins.input = entrada
        SportFalso.euler_codigo, SportFalso.modelo_low, SportFalso.ignorar_hasta = 0, False, 0.0
    t0 = REG[0][0]
    return r, buf.getvalue(), [(t - t0, n, a) for t, n, a in REG]


def ultima_prueba():
    runs = sorted((TMP / "experiments" / "uc13_gesture_follow").glob("*_probar_inclinacion"))
    if not runs:
        return {}, []
    man = json.loads((runs[-1] / "manifest.json").read_text())
    return man, list(csv.DictReader(open(runs[-1] / "serie.csv")))


r, out, reg = probar()
comprobar_invariantes(reg, out, bucle=False)
seq = [n for _, n, _ in reg]
eu = [(t, a[1]) for t, n, a in reg if n == "Euler"]
inc_p = [t for t, p in eu if p != 0.0]
fin0 = [t for t, p in eu if p == 0.0 and t > inc_p[-1]]
check(r == 0 and seq[0] == "BalanceStand" and inc_p and fin0, f"P: secuencia {seq[:5]}")
check(all(p in (0.0, SIGNO * MAX) for _, p in eu), "P: otro pitch")
check(len(inc_p) >= (CI["t_inclinar_s"] + 1.0) / PERIODO - 1, f"P: {len(inc_p)} Euler inclinado")
check(fin0[-1] - fin0[0] >= 2.0 - 0.15 and eu[-1][1] == 0.0, "P: Euler 0 menos de 2 s al final")
check("MotionSwitcher.CheckMode: {'form': '0', 'name': 'normal'}" in out
      and "sport API del ROBOT: 1.0.0.1" in out, "P: no muestra el diagnostico")
check("BalanceStand: codigo 0 = ok" in out and "mode: antes 0" in out, "P: codigo o mode")
check(f"Euler {'inclinado c1':<15}: {len(inc_p)} enviados" in out and "0 = ok (aceptado) x" in out
      and "Euler ACEPTADO" in out, "P: resumen de codigos")
check("IMU usada para medir: sportmodestate" in out and "ciclo 1: SE HA MOVIDO" in out
      and f"t_enderezar_s = {T_END}: OK" in out, "P: medida con sportmodestate")
print(f"  P. --probar-inclinacion: diagnostico, BalanceStand, {len(inc_p)} x "
      f"Euler({SIGNO * MAX:+.2f}) cada {PERIODO} s con su codigo, {len(fin0)} x Euler 0; "
      "mide con la IMU disponible")

# Robot que responde con 0.8 s de retraso, 2 ciclos, con --log: latencia y vuelta.
r, out, reg = probar(modelo=True, retardo=0.8, ciclos=2, espera_balance_s=0.5, log=True)
man, serie = ultima_prueba()
met = man.get("metricas", [])
check(len(met) == 2 and all(m["movido"] for m in met), f"P retardo: {met}")
check(all(0.7 <= m["latencia_s"] <= 1.0 for m in met), f"P retardo: latencias {met}")
check(all(m["vuelta_ok"] is False and 0.7 <= m["vuelta_s"] <= 1.0 for m in met),
      "P retardo: vuelta lenta no detectada")
check(all(m["desviacion_max_grados"] * SIGNO > 13 for m in met), "P retardo: desviacion")
check("tarda MAS que t_enderezar_s" in out and "OJO" in out, "P retardo: sin aviso de vuelta lenta")
check(man.get("fuente_imu") == "lowstate" and man.get("ciclos") == 2
      and len(serie) > 30 * 9 and {x["fase"] for x in serie} ==
      {"espera", "recto", "inclinado", "enderezando"}, f"P retardo: --log ({len(serie)} filas)")
print(f"  P. robot con 0.8 s de retraso: latencia {met[0]['latencia_s']:.2f}/"
      f"{met[1]['latencia_s']:.2f} s, vuelta {met[0]['vuelta_s']:.2f} s -> avisa que pasa de "
      f"t_enderezar_s; --log: serie.csv {len(serie)} filas y metricas en manifest.json")

# Primera secuencia ignorada (como en el robot): ciclo 1 no, ciclo 2 si -> PATRON.
r, out, reg = probar(modelo=True, retardo=0.1, ciclos=2, espera_balance_s=0.5,
                     ignorar_s=0.5 + 0.3 + 1.5 + 0.5, log=True)
man, _ = ultima_prueba()
met = man.get("metricas", [])
check(len(met) == 2 and not met[0]["movido"] and met[1]["movido"], f"P patron: {met}")
check("ciclo 1: NO se ha movido" in out and "ciclo 2: SE HA MOVIDO" in out
      and "PATRON" in out, "P patron: no lo explica")
print("  P. primera secuencia sin efecto: ciclo 1 NO, ciclo 2 SI -> lo dice (PATRON)")

# Robot que no implementa Euler: lo dice claramente.
r, out, reg = probar(euler_codigo=3203)
check(f"Euler({SIGNO * MAX:+.3f}) devolvio 3203 = el robot NO IMPLEMENTA" in out
      and "RECHAZA o no responde a Euler (3203" in out and "no implementa Euler" in out,
      "P: no explica el 3203")
print("  P. si el robot devuelve 3203 a Euler: lo dice (no implementado: firmware/servicio)")

# Por la linea de ordenes: --ciclos, --mantener y --espera-balance llegan a la prueba.
codigo, out, reg = ejecutar_main([("neutro", 1.0, 2.0, 0.0)],
                                 extra=("--probar-inclinacion", "--ciclos", "2",
                                        "--mantener", "0.5", "--espera-balance", "0.2"))
check(codigo == 0 and "espera 0.2 s" in out and "2 ciclo(s)" in out and "ciclo 2:" in out
      and "durante 1.0 s" in out, "P: opciones de la linea de ordenes")
print("  P. --probar-inclinacion --ciclos 2 --mantener 0.5 --espera-balance 0.2: llegan")

# --- Q. inclinacion.activa: false: ningun Euler, sigue igual -------------------
guion = [("dos_manos", 1.3, 2.0, 0.0), ("neutro", 3.0, 2.0, 0.0), ("neutro", 2.0, LEJOS, 0.0)]
codigo, out, reg = ejecutar_main(guion, cfg_extra={"inclinacion": {"activa": False}})
check(not any(n == "Euler" for _, n, _ in reg), "Q: Euler con la inclinacion desactivada")
check(any(a[0] > 0 for _, a in moves(reg)) and "-> seguir" in out, "Q: no sigue sin inclinacion")
print("  Q. con inclinacion.activa: false: 0 Euler; sigue y anda igual")

# --- S. saludo con la mano en el bucle completo ------------------------------------
def hellos(reg):
    return [t for t, n, _ in reg if n == "Hello"]


# S1. De pie y quieto: se inclina, saluda 4 s -> UN Hello (endereza antes), y
# después vuelve a inclinarse (BalanceStand tras Hello + espera_balance_s).
T_S = 2.0
guion = [("neutro", T_S, 2.0, 0.0), ("saludo", 4.0, 2.0, 0.0), ("neutro", 3.0, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion)
h = hellos(reg)
check(len(h) == 1, f"S1: {len(h)} Hello con 4 s saludando (re-armado)")
eu = [(t, a[1]) for t, n, a in reg if n == "Euler"]
check(any(p != 0.0 for t, p in eu if t < T_S), "S1: no estaba inclinado antes de saludar")
t_conf = next((float(l.split("[")[1].split("s]")[0]) for l in out.splitlines()
               if "gesto saludo, robot" in l), None)
check(t_conf is not None and t_conf - T_S <= 1.6,
      f"S1: saludo confirmado {None if t_conf is None else round(t_conf - T_S, 2)} s despues de empezar")
if h:
    antes = [p for t, p in eu if t < h[0]]
    check(antes and antes[-1] == 0.0, "S1: Hello sin enderezar antes")
    t_bal = next((t for t, n, _ in reg if n == "BalanceStand" and t > h[0]), None)
    t_inc = next((t for t, p in eu if p != 0.0 and t > h[0]), None)
    check(t_bal is not None and t_inc is not None and t_inc - t_bal >= ESPERA_BAL - 0.02,
          f"S1: no vuelve a inclinarse tras Hello (BalanceStand {t_bal}, Euler {t_inc})")
check("-> saludar" in out and "gesto saludo" in out, "S1: no lo dice")
check(not moves(reg), "S1: Move estando quieto")
print(f"  S1. de pie: saludar 4 s -> confirmado a los {t_conf - T_S:.2f} s, 1 Hello a los "
      f"{h[0] - T_S:.2f} s (tras enderezar) y "
      f"vuelve a inclinarse {t_inc - t_bal:.2f} s tras el BalanceStand")

# S2. De pie, una mano levantada QUIETA 4 s: nada.
codigo, out, reg = ejecutar_main([("neutro", 1.0, 2.0, 0.0), ("mano_i", 4.0, 2.0, 0.0)])
check(not hellos(reg) and "-> saludar" not in out, "S2: mano quieta dispara Hello")
print("  S2. de pie, mano levantada quieta 4 s: ningun Hello")

# S3. Tumbado y saludando (mano derecha): se levanta, y NO hace Hello después
# aunque siga saludando (re-armado); al volver a saludar, sí.
guion = [("saludo_d", 3.0, 2.0, 0.0), ("neutro", 1.5, 2.0, 0.0), ("saludo_d", 2.5, 2.0, 0.0)]
codigo, out, reg = ejecutar_main(guion, altura=0.07)
seq = nombres(reg)
check(seq[:2] == ["StandUp", "BalanceStand"], f"S3: no se levanta: {seq[:3]}")
t_h = hellos(reg)
check(len(t_h) == 1 and t_h[0] > 4.5, f"S3: Hello {t_h} (esperado uno solo, tras volver a saludar)")
print("  S3. tumbado: saludo -> StandUp; seguir saludando no hace Hello; al repetir, Hello")

# S4. Siguiendo: saludar no para ni hace Hello; sigue andando.
guion = [("dos_manos", 1.3, LEJOS, 0.0), ("neutro", 0.5, LEJOS, 0.0), ("saludo", 3.0, LEJOS, 0.0)]
codigo, out, reg = ejecutar_main(guion)
check(not hellos(reg) and out.count("-> parar") == 0, "S4: saludar siguiendo hace algo")
check(any(a[0] > 0 for t, a in moves(reg) if t > 2.5), "S4: deja de andar")
print("  S4. siguiendo: saludar no hace nada (ni Hello ni parar); sigue andando")

# S5. saludo.activo: false: nada (la T sigue saludando).
guion = [("neutro", 1.0, 2.0, 0.0), ("saludo", 3.0, 2.0, 0.0), ("neutro", 0.5, 2.0, 0.0),
         ("t", 1.3, 2.0, 0.0), ("neutro", 0.5, 2.0, 0.0)]
g_off = copy.deepcopy(CFG["gestos"])
g_off["saludo"]["activo"] = False
codigo, out, reg = ejecutar_main(guion, cfg_extra={"gestos": g_off})
check(len(hellos(reg)) == 1 and hellos(reg)[0] > 4.5, f"S5: Hello {hellos(reg)} con saludo off")
print("  S5. saludo.activo false: saludar no hace nada; la T sigue haciendo Hello")

# --- O. dry-run y --log ---------------------------------------------------------
guion = [("dos_manos", 1.3, LEJOS, 0.0), ("neutro", 1.5, LEJOS, 0.0)]
codigo, out, reg = ejecutar_main(guion, extra=("--dry-run",))
check(N_SPORT["n"] == 0 and not reg and "haria: SportClient.Move(" in out, "O: dry-run")
GF.paths.EXPERIMENTS = TMP / "experiments"
codigo, out, reg = ejecutar_main(guion, extra=("--log",))
runs = sorted((TMP / "experiments" / "uc13_gesture_follow").glob("*"))
check(bool(runs), "O: --log no escribe")
sys.path.insert(0, str(UC / "eval"))
import measure  # noqa: E402
if runs:
    filas = list(csv.DictReader(open(runs[-1] / "metrics.csv")))
    res = measure.medir(filas)
    check(all(c in filas[0] for c in ("saludo_cambios", "saludo_amplitud", "saludo_lado",
                                      "saludo_x_i", "saludo_x_d")) and "saludo" in res,
          "O: metrics.csv sin las columnas del saludo")
    check(json.loads((runs[-1] / "manifest.json").read_text())["usecase"] == "uc13_gesture_follow",
          "O: usecase")
    print(f"  O. dry-run: 0 SportClient; --log: {len(filas)} filas, vx_max {res['vx_max']}, "
          f"cuerpo {res.get('cuerpo')}")


# ===========================================================================
print("\n== 7b. identidad: seguir solo a quien hizo las dos manos ==")
from perception import identidad as ID  # noqa: E402

CID = CFG["identidad"]


def escena(personas_, seed=0):
    """Imagen 360x640 con fondo gris ruidoso y personas (x1, y1, x2, y2, camiseta, pantalon) BGR."""
    rng = np.random.default_rng(seed)
    img = rng.integers(90, 140, (360, 640, 3), dtype=np.uint8)
    for x1, y1, x2, y2, arriba, abajo in personas_:
        alto = y2 - y1
        img[y1:y1 + int(0.5 * alto), x1:x2] = arriba
        img[y1 + int(0.5 * alto):y2, x1:x2] = abajo
        ruido = rng.integers(-12, 12, (y2 - y1, x2 - x1, 3))
        img[y1:y2, x1:x2] = np.clip(img[y1:y2, x1:x2].astype(int) + ruido, 0, 255)
    return img


BLANCO, ROJO, AZUL, VERDE = (235, 235, 235), (40, 40, 220), (220, 60, 40), (60, 200, 60)
YO = (80, 40, 200, 330)          # pantalon rojo
OTRO = (380, 20, 560, 350)       # pantalon azul, mas grande (mas cerca)


def cand(img, caja):
    return elegir([(*caja, 0.9)], img.shape[1], img.shape[0])


img = escena([(*YO, BLANCO, ROJO), (*OTRO, BLANCO, AZUL)])
img2 = escena([(*YO, BLANCO, ROJO), (*OTRO, BLANCO, AZUL)], seed=1)
f_yo, f_otro = ID.firma(img, YO, CID), ID.firma(img, OTRO, CID)
f_yo2 = ID.firma(img2, YO, CID)
d_igual, d_otro = ID.distancia(f_yo, f_yo2, CID), ID.distancia(f_yo, f_otro, CID)
check(d_igual < 0.2, f"misma ropa con otro ruido: d {d_igual:.2f}")
check(d_otro > float(CID["umbral"]), f"otro pantalon: d {d_otro:.2f} no supera el umbral")

ide = ID.Identidad(CID)
c_yo, c_otro = cand(img, YO), cand(img, OTRO)
check(ID.mayor([c_yo, c_otro]).caja == c_otro.caja, "mayor no es la caja mas grande")
check(ID.de_la_pose([c_yo, c_otro], (100, 60, 180, 320)).caja == c_yo.caja,
      "de_la_pose no coge la caja de quien hace el gesto")
check(ide.fijar(img, c_yo, 0.0) and ide.activa, "no fija la firma")
p = ide.elegir(img2, [cand(img2, OTRO), cand(img2, YO)], 0.1)
check(p.visible and p.caja == c_yo.caja, "con otra persona mas cerca no me elige a mi")
p = ide.elegir(img2, [cand(img2, OTRO)], 0.2)
check(not p.visible, "sin mi, sigue al otro")
# Alguien con mi ropa que aparece de golpe al otro lado: no (si me vio hace poco).
gemelo = escena([(*OTRO, BLANCO, ROJO)], seed=2)
p = ide.elegir(gemelo, [cand(gemelo, OTRO)], 0.3)
check(not p.visible, "salta a alguien con mi ropa al otro lado de la imagen")
p = ide.elegir(gemelo, [cand(gemelo, OTRO)], 0.3 + float(CID["memoria_s"]) + 0.1)
check(p.visible, "tras memoria_s sin verme no me reconoce en otro sitio")
# Andando: la caja cortada por arriba (solo piernas). Se compara solo abajo.
CORTADA = (80, 0, 200, 330)
piernas = escena([(*CORTADA, ROJO, ROJO)], seed=3)
check(ID.firma(piernas, CORTADA, CID)["arriba"] is None, "caja cortada: compara la camiseta")
ide2 = ID.Identidad(CID)
ide2.fijar(img, c_yo, 0.0)
p = ide2.elegir(piernas, [cand(piernas, CORTADA)], 0.1)
check(p.visible, f"andando (solo piernas) no me reconoce: d {ide2.d_ultima}")
piernas_azules = escena([(*CORTADA, AZUL, AZUL)], seed=4)
p = ide2.elegir(piernas_azules, [cand(piernas_azules, CORTADA)], 0.2)
check(not p.visible, "andando sigue a otras piernas")
# Olvidar: vuelve a la mas grande.
ide.olvidar()
check(not ide.activa and ide.elegir(img, [c_yo, c_otro], 5.0).caja == c_otro.caja,
      "olvidar no vuelve a la persona mas grande")
# Imagen negra (sin color): no fija; se sigue como antes.
negra = np.zeros((360, 640, 3), np.uint8)
check(not ID.Identidad(CID).fijar(negra, cand(negra, YO), 0.0), "fija una firma de una imagen negra")
check(not ID.Identidad(dict(CID, activa=False)).fijar(img, c_yo, 0.0), "fija con activa: false")
print(f"  misma ropa d {d_igual:.2f}, otro pantalon d {d_otro:.2f} (umbral {CID['umbral']}); "
      "me elige con otro mas cerca; sin mi: no visible; gemelo al otro lado: no; "
      "solo piernas: compara abajo; olvidar -> la mas grande")


# ===========================================================================
print("\n== 8. control nuevo sobre los metrics.csv reales del 2026-11-18 ==")
runs = sorted(glob.glob(str(UC.parents[1] / "experiments" / "uc13_gesture_follow" / "2026-11-18T14*")))
for run in runs:
    filas = [x for x in csv.DictReader(open(Path(run) / "metrics.csv")) if x["modo"] == "SIGUIENDO"]
    if len(filas) < 100:
        continue
    viejo = measure.medir_seguimiento(filas)
    # Mismo error de distancia que vivió el robot (d - 4 m de entonces), ahora
    # respecto a 2 m.
    c = S.ControlSeguimiento(CS, LIM)
    ft = S.Filtro(CS["filtro"], CS["ventana"], CS["alfa"])
    nuevas, t_prev = [], None
    for x in filas:
        t = float(x["t"])
        dt = 1 / 15 if t_prev is None else min(t - t_prev, 0.2)
        t_prev = t
        d = float(x["distancia_m"]) - 2.0 if x["distancia_m"] else None
        d = ft.paso(d)
        lat = float(x["lateral"]) if x["lateral"] else None
        vx, _, wz = c.paso(d, lat, dt, t)
        nuevas.append({**x, "vx": f"{vx}", "wz": f"{wz}", "modo": "SIGUIENDO"})
    nuevo = measure.medir_seguimiento(nuevas)
    print(f"  {Path(run).name[:17]}: andando <0.2 m/s {viejo['andando_bajo_0_2_pct']}% -> "
          f"{nuevo['andando_bajo_0_2_pct']}%, arranques/min {viejo['arranques_por_min']} -> "
          f"{nuevo['arranques_por_min']}, giros/min {viejo['giros_por_min']} -> "
          f"{nuevo['giros_por_min']}, vx max {viejo['vx_max']} -> {nuevo['vx_max']}")
if not runs:
    print("  (no estan los experimentos de hoy: se omite)")

print(f"\n{N_OK} comprobaciones OK, {len(FALLOS)} fallos")
sys.exit(1 if FALLOS else 0)
