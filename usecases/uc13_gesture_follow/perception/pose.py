#!/usr/bin/env python3
"""usecases/uc13_gesture_follow/perception/pose.py

Detección de gestos y medida para el seguimiento, con MediaPipe PoseLandmarker.
Copia de la de uc12 con un gesto más (dos manos) y la medida de la persona
para seguirla. No importa nada de uc04, uc09, uc10, uc11 ni uc12.

    imagen ──► PoseLandmarker (hasta N personas) ──► la más grande ──► gesto
                                                                   └─► lateral, tam

Devuelve, por fotograma y EN BRUTO:
    gesto     "dos_manos", "mano", "cruzados", "t" o "ninguno"
    lateral   centro del TORSO respecto al centro de la imagen, en [-1, +1]
    tam       alto hombros -> tobillos, en fracción del alto de la imagen
No decide nada: confirmación, cooldown, acciones y control están en deploy/.

LOS GESTOS
----------
Coordenadas normalizadas de MediaPipe: x a la derecha, y HACIA ABAJO.

    dos_manos  las DOS muñecas por encima de la nariz (o de los hombros, según
               `referencia_mano`). Se comprueba PRIMERO.
    mano       UNA muñeca arriba, y la OTRA visible y por debajo de su hombro.
               Si la otra no se ve, o está a medio subir: ninguno. A 4 m, con
               la cámara a 30 cm del suelo, unas muñecas levantadas quedan en
               el borde de arriba de la imagen: es fácil que una se pierda, y
               sin esta regla "dos manos" se leería como "mano".
    cruzados   las dos muñecas cruzando la línea media, a la altura del pecho.
    t          brazos en cruz, horizontales (igual que uc12): cada muñeca
               hacia fuera de su hombro y a su altura, codos fuera, y ninguna
               muñeca por encima de la nariz ni "arriba".

EXCLUYENTES POR CONSTRUCCIÓN
----------------------------
    dos_manos / mano    mano exige la otra muñeca ABAJO; dos_manos, ARRIBA.
    t / dos_manos, mano la t exige que ninguna muñeca esté arriba.
    t / cruzados        cada muñeca en SU lado contra en el CONTRARIO.
Y si aun así se cumplen dos, el resultado es "ninguno".

EL SALUDO (gesto DINÁMICO, DetectorSaludo)
------------------------------------------
No sale de un fotograma sino de una ventana de tiempo (gestos.saludo):

    saludo     UNA muñeca "alzada" (por encima de su codo, o de su hombro,
               `referencia`, al menos `margen_anchos`) y la otra NO, y esa
               muñeca OSCILA en horizontal respecto a su hombro: en los
               últimos `ventana_s` al menos `cambios_min` cambios de
               dirección y una amplitud (pico a pico) de `amplitud_min`.
               Cualquiera de las dos manos.

    x  = (x muñeca - x hombro) / escala      (escala ~ anchos de hombro)
    serie x  ─► mediana de 3, SIN los extremos (quita atípicos de un
                fotograma, también del último: 1 fotograma de retraso)
             ─► cambio de dirección: la muñeca vuelve al menos `histeresis`
                desde el extremo (el temblor de MediaPipe no cuenta) Y el
                tramo que acaba en ese extremo mide al menos `amplitud_min`
                (un atípico de dos fotogramas da como mucho 2 tramos grandes;
                un saludo da 3 o más)
             ─► amplitud = máx - mín de la serie suavizada (para registrar)

Ventana de 3.0 s: en el robot (2026-11-24) se saludó a ~0.5-0.6 Hz con mucha
amplitud; con 1.5 s no caben 3 cambios.

La ventana ES la confirmación: en deploy/ no se le piden N fotogramas más.
Una mano levantada QUIETA no oscila y una mano que BAJA deja de estar alzada
(se borra su historia). Re-armado: tras actuar, bloquear() deja el saludo sin
detectar hasta que se ve el brazo BAJADO `hueco_max_s` (en fotogramas
procesados: si el bucle estuvo ocupado con Hello y la persona sigue
saludando, NO se vuelve a lanzar). Es independiente de clasificar(): el bucle
sustituye el gesto del fotograma por "saludo" cuando la ventana lo detecta.

LA MEDIDA PARA SEGUIR
---------------------
Se usan el TORSO y las PIERNAS, nunca los brazos ni la cabeza: con las manos
arriba o en T, la caja de todos los puntos crece y se desplaza, y el robot
creería que la persona se ha acercado o movido. `tam` es la distancia vertical
de los hombros a los tobillos; si no se ven los dos, no hay medida (el robot
no avanza, solo gira).

Uso como programa, para probar con la webcam sin robot:

    python3 usecases/uc13_gesture_follow/perception/pose.py --show
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

NINGUNO, MANO, CRUZADOS, T, DOS = "ninguno", "mano", "cruzados", "t", "dos_manos"
SALUDO = "saludo"
GESTOS = (DOS, MANO, CRUZADOS, T)          # los de UN fotograma (clasificar)

# Color de cada gesto en --show (BGR).
COLORES = {MANO: (0, 200, 0), CRUZADOS: (0, 140, 255), T: (255, 0, 220),
           DOS: (255, 255, 0), SALUDO: (0, 255, 255)}
GRIS = (200, 200, 200)

# Índices de los landmarks de MediaPipe Pose que se usan.
NARIZ = 0
HOMBRO_I, HOMBRO_D = 11, 12
CODO_I, CODO_D = 13, 14
MUNECA_I, MUNECA_D = 15, 16
CADERA_I, CADERA_D = 23, 24
TOBILLO_I, TOBILLO_D = 27, 28

# Esqueleto para dibujar. Se define aquí para no depender de mp.solutions,
# que algunas versiones recientes de mediapipe ya no traen.
CONEXIONES = [
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),   # brazos y hombros
    (11, 23), (12, 24), (23, 24),                       # tronco
    (23, 25), (25, 27), (24, 26), (26, 28),             # piernas
    (0, 11), (0, 12),                                   # cabeza a hombros
]


@dataclass
class Pose:
    """La persona elegida en un fotograma."""

    visible: bool
    gesto: str = NINGUNO
    puntos: np.ndarray | None = None    # (33, 3): x, y, visibilidad
    caja: tuple[int, int, int, int] | None = None
    n_personas: int = 0
    medidas_t: str = ""                 # para --debug: lo que mide la T
    lateral: float | None = None        # centro del torso, [-1, +1]
    tam: float | None = None            # hombros -> tobillos, fracción del alto


# ===========================================================================
# Clasificación. Funciones puras sobre un array (33, 3) para poder probarlas
# sin cámara ni MediaPipe.
# ===========================================================================
def _ve(p: np.ndarray, i: int, vis_min: float) -> bool:
    return bool(p[i, 2] >= vis_min)


def _referencia_arriba(p: np.ndarray, cfg: dict) -> tuple[float, float] | None:
    """(y de referencia, margen) para decir que una muñeca está "arriba"."""
    vis = float(cfg["vis_min"])
    margen = float(cfg["margen_cabeza"])
    if cfg.get("referencia_mano", "nariz") == "hombros":
        if not (_ve(p, HOMBRO_I, vis) and _ve(p, HOMBRO_D, vis)):
            return None
        # Además, al menos `margen_hombros_anchos` anchos de hombros por
        # encima: deja una zona muerta con la T (que llega a t.altura_max).
        ancho = abs(p[HOMBRO_I, 0] - p[HOMBRO_D, 0])
        margen = max(margen, float(cfg.get("margen_hombros_anchos", 1.0)) * ancho)
        return float(min(p[HOMBRO_I, 1], p[HOMBRO_D, 1])), margen
    if not _ve(p, NARIZ, vis):
        return None               # sin cabeza no se puede decir "por encima"
    return float(p[NARIZ, 1]), margen


def _arriba(p: np.ndarray, cfg: dict, muneca: int) -> bool:
    ref = _referencia_arriba(p, cfg)
    if ref is None or not _ve(p, muneca, float(cfg["vis_min"])):
        return False
    # y crece hacia abajo: "por encima" es una y MENOR
    return bool(p[muneca, 1] < ref[0] - ref[1])


def dos_manos(p: np.ndarray, cfg: dict) -> bool:
    return _arriba(p, cfg, MUNECA_I) and _arriba(p, cfg, MUNECA_D)


def mano_levantada(p: np.ndarray, cfg: dict) -> bool:
    """UNA muñeca arriba y la otra visible y por debajo de SU hombro."""
    vis = float(cfg["vis_min"])
    if not (_ve(p, HOMBRO_I, vis) and _ve(p, HOMBRO_D, vis)):
        return False
    ancho = abs(p[HOMBRO_I, 0] - p[HOMBRO_D, 0])
    bajo = float(cfg["mano"]["otra_bajo_hombro_anchos"]) * ancho
    for arriba, otra, hombro_otra in ((MUNECA_I, MUNECA_D, HOMBRO_D),
                                      (MUNECA_D, MUNECA_I, HOMBRO_I)):
        # La otra NO puede estar "arriba": así "mano" y "dos manos" son
        # excluyentes por definición, sin depender de dónde esté la nariz.
        if not _arriba(p, cfg, arriba) or _arriba(p, cfg, otra):
            continue
        if _ve(p, otra, vis) and p[otra, 1] > p[hombro_otra, 1] + bajo:
            return True
    return False


def brazos_cruzados(p: np.ndarray, cfg: dict) -> bool:
    vis = float(cfg["vis_min"])
    if not all(_ve(p, i, vis) for i in (HOMBRO_I, HOMBRO_D, MUNECA_I, MUNECA_D)):
        return False

    hi, hd = p[HOMBRO_I], p[HOMBRO_D]
    medio = (hi[0] + hd[0]) / 2.0
    ancho = abs(hi[0] - hd[0])
    if ancho < 1e-3:
        return False              # de perfil: no se puede saber si cruza

    # Cada muñeca al otro lado de la línea media, y pasada un mínimo para que
    # unas manos juntas en el centro no cuenten como cruzadas.
    cruce = float(cfg["cruce_min"]) * ancho
    mi, md = p[MUNECA_I], p[MUNECA_D]
    lado_hi = np.sign(hi[0] - medio)
    lado_hd = np.sign(hd[0] - medio)
    if not ((mi[0] - medio) * lado_hd > cruce and (md[0] - medio) * lado_hi > cruce):
        return False

    # Banda del pecho: entre los hombros y las caderas. Si no se ven las
    # caderas, el límite inferior se estima con la anchura de hombros.
    margen = float(cfg["margen_pecho"])
    arriba = min(hi[1], hd[1]) - margen
    if _ve(p, CADERA_I, vis) and _ve(p, CADERA_D, vis):
        abajo = (p[CADERA_I, 1] + p[CADERA_D, 1]) / 2.0 + margen
    else:
        abajo = max(hi[1], hd[1]) + 1.5 * ancho
    return all(arriba <= m[1] <= abajo for m in (mi, md))


def _geometria_t(p: np.ndarray, cfg: dict) -> dict | None:
    """Medidas de la T en anchos de hombros, o None si no se puede medir."""
    vis = float(cfg["vis_min"])
    if not all(_ve(p, i, vis) for i in (HOMBRO_I, HOMBRO_D, MUNECA_I, MUNECA_D)):
        return None
    hi, hd = p[HOMBRO_I], p[HOMBRO_D]
    ancho = abs(hi[0] - hd[0])
    if ancho < 1e-3:
        return None               # de perfil
    medio = (hi[0] + hd[0]) / 2.0
    res = {}
    for lado, h, m, c in (("i", hi, p[MUNECA_I], CODO_I), ("d", hd, p[MUNECA_D], CODO_D)):
        fuera = np.sign(h[0] - medio)      # hacia dónde es "fuera" en este lado
        res[f"sep_{lado}"] = float((m[0] - h[0]) * fuera / ancho)
        res[f"dy_{lado}"] = float(abs(m[1] - h[1]) / ancho)
        res[f"codo_{lado}"] = (float((p[c, 0] - h[0]) * fuera / ancho)
                               if _ve(p, c, vis) else None)
    return res


def brazos_en_t(p: np.ndarray, cfg: dict) -> bool:
    ct = cfg["t"]
    g = _geometria_t(p, cfg)
    if g is None:
        return False
    for lado in ("i", "d"):
        if g[f"sep_{lado}"] < float(ct["separacion_min"]):
            return False
        if g[f"dy_{lado}"] > float(ct["altura_max"]):
            return False
        if ct.get("exigir_codos", True):
            codo = g[f"codo_{lado}"]
            if codo is None or codo < float(ct["codo_fuera_min"]):
                return False
    # Exclusión con "mano" y "dos manos": ninguna muñeca por encima de la
    # nariz (si se ve) ni "arriba" según la referencia configurada.
    vis = float(cfg["vis_min"])
    if _ve(p, NARIZ, vis) and min(p[MUNECA_I, 1], p[MUNECA_D, 1]) < p[NARIZ, 1]:
        return False
    return not (_arriba(p, cfg, MUNECA_I) or _arriba(p, cfg, MUNECA_D))


def texto_t(p: np.ndarray, cfg: dict) -> str:
    """Resumen de las medidas de la T para --debug, en anchos de hombros."""
    g = _geometria_t(p, cfg)
    if g is None:
        return "t: no medible"

    def f(v):
        return "--" if v is None else f"{v:.2f}"
    return (f"t: sep {f(g['sep_i'])}/{f(g['sep_d'])} dy {f(g['dy_i'])}/{f(g['dy_d'])} "
            f"codo {f(g['codo_i'])}/{f(g['codo_d'])}")


def clasificar(p: np.ndarray, cfg: dict) -> str:
    """Primero dos manos; después, uno y solo uno de los otros tres."""
    if dos_manos(p, cfg):
        otros = (mano_levantada(p, cfg) or brazos_cruzados(p, cfg)
                 or brazos_en_t(p, cfg))
        return NINGUNO if otros else DOS
    activos = [g for g, ok in ((MANO, mano_levantada(p, cfg)),
                               (CRUZADOS, brazos_cruzados(p, cfg)),
                               (T, brazos_en_t(p, cfg))) if ok]
    if len(activos) != 1:
        return NINGUNO            # ninguno, o ambiguo: no se hace nada
    return activos[0]


# ===========================================================================
# Saludo (dinámico). Funciones puras + un acumulador por lado.
# ===========================================================================
# lado -> (hombro, codo, muñeca, cadera)
LADOS = {"i": (HOMBRO_I, CODO_I, MUNECA_I, CADERA_I),
         "d": (HOMBRO_D, CODO_D, MUNECA_D, CADERA_D)}


def escala_saludo(p: np.ndarray, cs: dict, vis_min: float) -> float | None:
    """Unidad de medida del saludo: el ancho de hombros, pero nunca menos de
    `escala_min_torso` veces el largo del torso (hombros -> caderas). De
    frente es el ancho de hombros; de lado el ancho se encoge y el torso
    evita que el temblor se dispare al dividir. None si no hay hombros."""
    if not (_ve(p, HOMBRO_I, vis_min) and _ve(p, HOMBRO_D, vis_min)):
        return None
    ancho = float(abs(p[HOMBRO_I, 0] - p[HOMBRO_D, 0]))
    if _ve(p, CADERA_I, vis_min) and _ve(p, CADERA_D, vis_min):
        torso = float(abs((p[CADERA_I, 1] + p[CADERA_D, 1]) / 2
                          - (p[HOMBRO_I, 1] + p[HOMBRO_D, 1]) / 2))
        ancho = max(ancho, float(cs.get("escala_min_torso", 0.0)) * torso)
    return ancho if ancho > 1e-3 else None


def brazo_alzado(p: np.ndarray, cs: dict, vis_min: float, lado: str,
                 escala: float, cfg_gestos: dict | None = None) -> bool:
    """Muñeca por encima de su codo (o de su hombro) al menos margen_anchos,
    O "arriba" con el mismo criterio que "una mano" (cfg_gestos).

    Lo segundo hace falta (dry-run 2026-11-24): saludando, en el extremo del
    vaivén hacia dentro la muñeca seguía por encima de la nariz ("mano") pero
    no por encima del codo (o el codo dejó de verse) durante 4-11 fotogramas;
    eso borraba la historia justo en el cambio de dirección."""
    hombro, codo, muneca, _ = LADOS[lado]
    if cfg_gestos is not None and _arriba(p, cfg_gestos, muneca):
        return True
    ref = hombro if cs.get("referencia", "codo") == "hombro" else codo
    if not (_ve(p, muneca, vis_min) and _ve(p, ref, vis_min)):
        return False
    # y crece hacia abajo: "por encima" es una y MENOR
    return bool(p[muneca, 1] < p[ref, 1] - float(cs["margen_anchos"]) * escala)


def x_saludo(p: np.ndarray, lado: str, escala: float) -> float:
    """Posición horizontal de la muñeca respecto a SU hombro, en escalas."""
    hombro, _, muneca, _ = LADOS[lado]
    return float((p[muneca, 0] - p[hombro, 0]) / escala)


def mediana3(xs: list[float]) -> list[float]:
    """Mediana móvil de 3, solo donde la ventana está completa: se pierden el
    primero y el ÚLTIMO (si no, un atípico en el último fotograma pasaría)."""
    return [float(np.median(xs[i - 1:i + 2])) for i in range(1, len(xs) - 1)]


def oscilacion(xs: list[float], histeresis: float,
               amplitud_min: float = 0.0) -> tuple[int, float]:
    """(cambios de dirección, amplitud pico a pico) de una serie.

    Un cambio de dirección cuenta cuando la serie (suavizada con mediana de
    3) vuelve al menos `histeresis` desde el último extremo, y el tramo que
    llega a ese extremo (desde el extremo anterior) mide al menos
    `amplitud_min`.
    """
    s = mediana3(list(xs))
    if len(s) < 2:
        return 0, 0.0
    amplitud = float(max(s) - min(s))
    cambios, direccion = 0, 0
    lo = hi = ext = ini = s[0]
    for x in s[1:]:
        if direccion == 0:
            lo, hi = min(lo, x), max(hi, x)
            if x - lo >= histeresis:
                direccion, ext, ini = 1, x, lo
            elif hi - x >= histeresis:
                direccion, ext, ini = -1, x, hi
        elif (x - ext) * direccion > 0:
            ext = x                                   # sigue en la misma dirección
        elif (ext - x) * direccion >= histeresis:
            if abs(ext - ini) >= amplitud_min:
                cambios += 1
            direccion, ini, ext = -direccion, ext, x
    return cambios, amplitud


@dataclass
class MedidaSaludo:
    detectado: bool = False
    cambios: int = 0
    amplitud: float = 0.0
    lado: str = ""                      # "i", "d" o "" (ninguno alzado)
    x: dict | None = None               # lado -> x de este fotograma (si alzado)
    bloqueado: bool = False             # re-armado: esperando a que baje el brazo


class DetectorSaludo:
    """Ventana deslizante por lado. paso(t, puntos) en cada fotograma con
    MediaPipe; puntos None si no se ve a nadie."""

    def __init__(self, cfg_gestos: dict) -> None:
        self.cfg_gestos = cfg_gestos
        self.cs = cfg_gestos["saludo"]
        self.activo = bool(self.cs.get("activo", True))
        self.vis_min = float(cfg_gestos["vis_min"])
        self.ventana = float(self.cs["ventana_s"])
        self.hueco_max = float(self.cs.get("hueco_max_s", 0.3))
        self.hist: dict[str, list[tuple[float, float]]] = {"i": [], "d": []}
        # Desde cuándo (en fotogramas PROCESADOS) no se ve ese brazo válido.
        self.t_no: dict[str, float | None] = {"i": None, "d": None}
        self.bloqueado = False
        self.t_bajado: float | None = None

    def reiniciar(self) -> None:
        """Vacía la ventana (watchdog). NO quita el bloqueo del re-armado."""
        for lado in self.hist:
            self.hist[lado] = []
            self.t_no[lado] = None

    def bloquear(self) -> None:
        """El saludo ha actuado: no se vuelve a detectar hasta ver el brazo
        bajado `hueco_max_s` (aunque mientras tanto no lleguen fotogramas)."""
        self.bloqueado = True
        self.t_bajado = None

    def paso(self, t: float, p: np.ndarray | None) -> MedidaSaludo:
        alzados: dict[str, float] = {}
        if p is not None:
            escala = escala_saludo(p, self.cs, self.vis_min)
            if escala is not None:
                for lado in ("i", "d"):
                    if brazo_alzado(p, self.cs, self.vis_min, lado, escala, self.cfg_gestos):
                        alzados[lado] = x_saludo(p, lado, escala)
        # Las dos alzadas no es un saludo (dos manos, o ambiguo).
        validos = alzados if len(alzados) == 1 else {}
        for lado in ("i", "d"):
            h = self.hist[lado]
            if lado in validos:
                h.append((t, validos[lado]))
                self.t_no[lado] = None
            else:
                self.t_no[lado] = self.t_no[lado] if self.t_no[lado] is not None else t
                if t - self.t_no[lado] >= self.hueco_max:
                    h.clear()                 # bajó la mano o dejó de verse
            while h and h[0][0] < t - self.ventana:
                h.pop(0)
        # Re-armado: ningún brazo alzado durante hueco_max_s.
        if self.bloqueado:
            if alzados:
                self.t_bajado = None
            else:
                self.t_bajado = self.t_bajado if self.t_bajado is not None else t
                if t - self.t_bajado >= self.hueco_max:
                    self.bloqueado = False

        mejor = MedidaSaludo(x=alzados or None, bloqueado=self.bloqueado,
                             lado=next(iter(validos), ""))
        # Se mide con la historia aunque en ESTE fotograma no se vea el brazo:
        # un fotograma suelto sin brazo (menos de hueco_max_s) no la borra y
        # tampoco debe dar 0 cambios.
        for lado in ("i", "d"):
            if not self.hist[lado]:
                continue
            cambios, amp = oscilacion([x for _, x in self.hist[lado]],
                                      float(self.cs["histeresis"]),
                                      float(self.cs["amplitud_min"]))
            if (cambios, amp) > (mejor.cambios, mejor.amplitud):
                mejor.cambios, mejor.amplitud, mejor.lado = cambios, amp, lado
        mejor.detectado = (self.activo and not self.bloqueado
                           and mejor.cambios >= int(self.cs["cambios_min"])
                           and mejor.amplitud >= float(self.cs["amplitud_min"]))
        return mejor


# ===========================================================================
# Medida para el seguimiento
# ===========================================================================
def medir_persona(p: np.ndarray, vis_min: float) -> tuple[float | None, float | None]:
    """(lateral, tam) a partir del torso y las piernas, sin brazos ni cabeza.

    lateral  media x de hombros y caderas visibles, en [-1, +1]
             (+ = persona a la derecha de la imagen). None si no hay torso.
    tam      y tobillos - y hombros (medias de los visibles), fracción del
             alto de la imagen. None si faltan hombros o tobillos.
    """
    torso = [i for i in (HOMBRO_I, HOMBRO_D, CADERA_I, CADERA_D) if _ve(p, i, vis_min)]
    lateral = None
    if len(torso) >= 2:
        lateral = float((np.mean(p[torso, 0]) - 0.5) / 0.5)
    hombros = [i for i in (HOMBRO_I, HOMBRO_D) if _ve(p, i, vis_min)]
    tobillos = [i for i in (TOBILLO_I, TOBILLO_D) if _ve(p, i, vis_min)]
    tam = None
    if hombros and tobillos:
        t = float(np.mean(p[tobillos, 1]) - np.mean(p[hombros, 1]))
        tam = t if t > 0 else None
    return lateral, tam


def area_visible(p: np.ndarray, vis_min: float) -> float:
    v = p[p[:, 2] >= vis_min]
    if len(v) < 4:
        return 0.0
    return float((v[:, 0].max() - v[:, 0].min()) * (v[:, 1].max() - v[:, 1].min()))


# ===========================================================================
class DetectorGestos:
    """Cámara + PoseLandmarker + elección de la persona más grande."""

    def __init__(self, cfg: dict, camara: int = 0, fuente: str = "webcam",
                 iface: str = "enp3s0", domain: int = 0,
                 init_dds: bool = True) -> None:
        self.cfg = cfg
        self.vis_min = float(cfg["gestos"]["vis_min"])
        self.cfg_gestos = cfg["gestos"]
        self.ancho_salida = int(cfg.get("ancho", 640))

        modelo = Path(cfg["modelo_pose"])
        if not modelo.is_absolute():
            modelo = Path(__file__).resolve().parents[3] / modelo
        if not modelo.exists():
            raise RuntimeError(
                f"No existe el modelo de pose: {modelo}\n"
                "Descargalo (ver usecases/uc13_gesture_follow/README.md,\n"
                "'Dependencia: MediaPipe') o cambia `modelo_pose` en gesture_follow.yaml.")

        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
        except ImportError as e:
            raise RuntimeError(
                "Falta mediapipe en esta caja. Ver el README de uc13, "
                "'Dependencia: MediaPipe'.") from e

        self._mp = mp
        opciones = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(modelo)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=int(cfg.get("num_poses", 3)),
            min_pose_detection_confidence=float(cfg.get("confianza_min", 0.5)),
            min_pose_presence_confidence=float(cfg.get("confianza_min", 0.5)),
            min_tracking_confidence=float(cfg.get("confianza_min", 0.5)),
        )
        self.pose = vision.PoseLandmarker.create_from_options(opciones)
        self._t0 = time.monotonic()
        self._ts_prev = -1

        # Dos fuentes, igual que uc04. La del robot llega por la red con
        # VideoClient; timeout corto para no quedarse 3 s bloqueado si se corta.
        self.fuente = fuente
        if fuente == "robot":
            sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
            from robot_camera import CamaraRobot
            self.cam = CamaraRobot(iface, domain, timeout=1.0, init_dds=init_dds)
            self.cap = None
        else:
            self.cam = None
            self.cap = cv2.VideoCapture(camara)
            if not self.cap.isOpened():
                raise RuntimeError(
                    f"No se puede abrir /dev/video{camara}.\n"
                    "En Linux hay que pasar la camara al contenedor: usa ./go2 en\n"
                    "lugar de `docker compose`, que es quien carga el override.")
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self.fps = 0.0
        self._t_prev = time.perf_counter()
        self._firma_prev: int | None = None
        self.motivo_sin_imagen = ""      # "repetida" o "fallo", para --debug

    # ------------------------------------------------------------------
    def leer_imagen(self) -> np.ndarray | None:
        """Un fotograma NUEVO, o None si falla o si es igual al anterior.

        Un driver colgado puede devolver el mismo buffer una y otra vez. Con
        ruido de sensor real dos fotogramas nunca son idénticos, así que uno
        repetido se trata como si no hubiera llegado: así el watchdog lo ve.
        """
        if self.cam is not None:
            imagen = self.cam.leer()
        else:
            ok, imagen = self.cap.read()
            imagen = imagen if ok else None
        if imagen is None:
            self.motivo_sin_imagen = "fallo"
            return None

        firma = hash(imagen[::16, ::16].tobytes())
        if firma == self._firma_prev:
            # La camara del robot devuelve el ULTIMO fotograma: si se le pide
            # antes de que tenga otro, llega el mismo. Normal a ~15 fps.
            self.motivo_sin_imagen = "repetida"
            return None
        self._firma_prev = firma

        if self.ancho_salida and imagen.shape[1] > self.ancho_salida:
            e = self.ancho_salida / imagen.shape[1]
            imagen = cv2.resize(imagen, None, fx=e, fy=e, interpolation=cv2.INTER_AREA)
        return imagen

    def detectar(self, imagen: np.ndarray) -> Pose:
        rgb = cv2.cvtColor(imagen, cv2.COLOR_BGR2RGB)
        mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        # El modo VIDEO exige marcas de tiempo estrictamente crecientes.
        ts = int((time.monotonic() - self._t0) * 1000)
        ts = max(ts, self._ts_prev + 1)
        self._ts_prev = ts
        res = self.pose.detect_for_video(mp_img, ts)

        ahora = time.perf_counter()
        dt = ahora - self._t_prev
        self._t_prev = ahora
        if dt > 0:
            self.fps = 0.8 * self.fps + 0.2 * (1.0 / dt)

        mejor, mejor_area = None, 0.0
        for persona in res.pose_landmarks or []:
            p = np.array([[l.x, l.y, (l.visibility or 0.0)] for l in persona],
                         dtype=np.float32)
            a = area_visible(p, self.vis_min)
            if a > mejor_area:
                mejor, mejor_area = p, a

        n = len(res.pose_landmarks or [])
        if mejor is None:
            return Pose(visible=False, n_personas=n)
        return pose_desde_puntos(mejor, self.cfg_gestos, imagen.shape, n)

    # ------------------------------------------------------------------
    def dibujar(self, imagen: np.ndarray, pose: Pose, lineas: list[str]) -> np.ndarray:
        """Esqueleto, medida de seguimiento y líneas de texto de estado.

        El esqueleto y la PRIMERA línea (gesto y contador) van en el color del
        gesto: cian dos manos, verde mano, naranja cruzados, magenta t.
        En blanco, la medida que usa el seguimiento: línea de hombros, línea
        de tobillos y centro del torso.
        """
        return dibujar(imagen, pose, lineas, self.vis_min, self.fps)

    def cerrar(self) -> None:
        if self.cap is not None:
            self.cap.release()
        try:
            self.pose.close()
        except Exception:
            pass


def pose_desde_puntos(p: np.ndarray, cfg_gestos: dict, forma: tuple,
                      n_personas: int = 1) -> Pose:
    """Pose completa (gesto + medida) a partir de los 33 puntos."""
    vis = float(cfg_gestos["vis_min"])
    h, w = forma[:2]
    v = p[p[:, 2] >= vis]
    caja = None
    if len(v):
        caja = (int(v[:, 0].min() * w), int(v[:, 1].min() * h),
                int(v[:, 0].max() * w), int(v[:, 1].max() * h))
    lateral, tam = medir_persona(p, vis)
    return Pose(visible=True, gesto=clasificar(p, cfg_gestos), puntos=p, caja=caja,
                n_personas=n_personas, medidas_t=texto_t(p, cfg_gestos),
                lateral=lateral, tam=tam)


def dibujar(imagen: np.ndarray, pose: Pose, lineas: list[str], vis_min: float,
            fps: float) -> np.ndarray:
    h, w = imagen.shape[:2]
    vis = imagen.copy()
    color = COLORES.get(pose.gesto, GRIS) if pose.visible else GRIS
    if pose.visible and pose.puntos is not None:
        p = pose.puntos
        for a, b in CONEXIONES:
            if p[a, 2] >= vis_min and p[b, 2] >= vis_min:
                cv2.line(vis, (int(p[a, 0] * w), int(p[a, 1] * h)),
                         (int(p[b, 0] * w), int(p[b, 1] * h)), color, 2)
        for i in range(len(p)):
            if p[i, 2] >= vis_min:
                cv2.circle(vis, (int(p[i, 0] * w), int(p[i, 1] * h)), 3, color, -1)
        # Medida del seguimiento, en blanco.
        if pose.lateral is not None:
            x = int((pose.lateral * 0.5 + 0.5) * w)
            cv2.line(vis, (x, 0), (x, h), (255, 255, 255), 1)
        hombros = [i for i in (HOMBRO_I, HOMBRO_D) if p[i, 2] >= vis_min]
        tobillos = [i for i in (TOBILLO_I, TOBILLO_D) if p[i, 2] >= vis_min]
        if pose.tam is not None and hombros and tobillos:
            for grupo in (hombros, tobillos):
                y = int(np.mean(p[grupo, 1]) * h)
                cv2.line(vis, (int(w * 0.35), y), (int(w * 0.65), y), (255, 255, 255), 1)

    # Centro de la imagen y zona a la que se quiere llevar el torso.
    cv2.line(vis, (w // 2, h - 20), (w // 2, h), (180, 180, 180), 1)

    for k, texto in enumerate(lineas):
        cv2.putText(vis, texto, (10, 24 + 22 * k), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 0, 0), 3)
        cv2.putText(vis, texto, (10, 24 + 22 * k), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, color if k == 0 else (0, 230, 230), 1)
    cv2.putText(vis, f"{fps:4.1f} fps", (w - 95, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
    return vis


# ===========================================================================
if __name__ == "__main__":
    import argparse

    import yaml

    p = argparse.ArgumentParser(description="Prueba de gestos con la webcam, sin robot")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--config", default=str(Path(__file__).resolve().parents[1]
                                           / "configs" / "gesture_follow.yaml"))
    p.add_argument("--show", action="store_true")
    p.add_argument("--seconds", type=float, default=30.0)
    args = p.parse_args()

    cfg_yaml = yaml.safe_load(Path(args.config).read_text())
    det = DetectorGestos(cfg_yaml, camara=args.camera)
    saludo = DetectorSaludo(cfg_yaml["gestos"])
    print("detector listo. Ctrl-C para salir.\n")
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < args.seconds:
            img = det.leer_imagen()
            if img is None:
                time.sleep(0.01)
                continue
            pose = det.detectar(img)
            ms = saludo.paso(time.monotonic(), pose.puntos if pose.visible else None)
            if ms.detectado:
                pose.gesto = SALUDO
            tam = "--" if pose.tam is None else f"{pose.tam:.3f}"
            lat = "--" if pose.lateral is None else f"{pose.lateral:+.2f}"
            txt_sal = f"saludo: cambios {ms.cambios} amplitud {ms.amplitud:.2f} lado {ms.lado or '-'}"
            if args.show:
                cv2.imshow("uc13 - gestos", det.dibujar(
                    img, pose, [f"gesto: {pose.gesto}", f"tam {tam}  lateral {lat}",
                                pose.medidas_t, txt_sal]))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                print(f"\r  personas {pose.n_personas}  gesto {pose.gesto:<9} "
                      f"{txt_sal}  {det.fps:4.1f} fps", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        det.cerrar()
        cv2.destroyAllWindows()
        print("\nfin")
