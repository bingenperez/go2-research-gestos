"""usecases/uc13_gesture_follow/perception/identidad.py

Seguir SOLO a quien hizo el gesto de dos manos, aunque haya más gente.

Antes se seguía en cada fotograma a la caja de YOLO más grande: si alguien se
cruzaba más cerca, el robot se iba con esa persona.

    dos manos (empieza a seguir) ──► fijar(): "firma" de colores de su ropa
    siguiendo, cada fotograma    ──► elegir(): de todas las cajas, la que más
                                     se parece a la firma Y está cerca de
                                     donde se le vio la última vez
    nadie se parece lo bastante  ──► no visible: el robot se para (PAUSADO) y
                                     a los pausa_max_s deja de seguir
    deja de seguir               ──► olvidar()

Firma = histogramas de color (H y S de HSV, sin los píxeles muy oscuros) de
dos franjas de la caja de YOLO:

    arriba  [arriba_desde, arriba_hasta] del alto de la caja  (camiseta)
    abajo   [abajo_desde, 1]                                  (pantalón y zapatos)

Andando a 2 m el robot solo ve piernas (la caja queda cortada por arriba), así
que manda la franja de ABAJO: pesa `peso_abajo`, y si la caja toca el borde de
arriba de la imagen la franja de arriba no se compara (no es la camiseta).

Distancia entre firmas: Bhattacharyya (0 = iguales, 1 = nada que ver).
Umbrales SIN MEDIR en el robot: mirar `id_d` en --debug y ajustarlos.
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from perception.personas import Persona


def _hist(imagen: np.ndarray, x1: int, y1: int, x2: int, y2: int,
          cfg: dict) -> np.ndarray | None:
    """Histograma H-S normalizado de un recorte BGR, o None si es muy pequeño."""
    h, w = imagen.shape[:2]
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    hsv = cv2.cvtColor(imagen[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    # Fuera los píxeles casi negros: sombras y fondo oscuro no son ropa.
    mascara = cv2.inRange(hsv, (0, 0, int(cfg.get("v_min", 40))), (180, 255, 255))
    if cv2.countNonZero(mascara) < 16:
        return None
    hist = cv2.calcHist([hsv], [0, 1], mascara,
                        [int(cfg.get("bins_h", 18)), int(cfg.get("bins_s", 8))],
                        [0, 180, 0, 256])
    cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
    return hist


def cortada_arriba(caja: tuple[int, int, int, int], alto_imagen: int, cfg: dict) -> bool:
    """La caja toca el borde de arriba: la cabeza (y quizá el torso) no se ve."""
    return caja[1] <= float(cfg.get("borde_arriba", 0.02)) * alto_imagen


def firma(imagen: np.ndarray, caja: tuple[int, int, int, int], cfg: dict) -> dict:
    """{"arriba": hist | None, "abajo": hist | None} de una caja de YOLO."""
    x1, y1, x2, y2 = caja
    alto = y2 - y1
    # Un poco hacia dentro por los lados: la caja de YOLO coge fondo.
    m = int(0.15 * (x2 - x1))
    a0, a1 = (float(v) for v in cfg.get("arriba", [0.15, 0.5]))
    b0 = float(cfg.get("abajo_desde", 0.55))
    arriba = None
    if not cortada_arriba(caja, imagen.shape[0], cfg):
        arriba = _hist(imagen, x1 + m, y1 + int(a0 * alto), x2 - m, y1 + int(a1 * alto), cfg)
    abajo = _hist(imagen, x1 + m, y1 + int(b0 * alto), x2 - m, y2, cfg)
    return {"arriba": arriba, "abajo": abajo}


def distancia(a: dict, b: dict, cfg: dict) -> float:
    """0 = misma ropa, 1 = nada que ver. Solo compara las franjas que tienen
    las dos firmas; sin ninguna en común, 1."""
    peso_abajo = float(cfg.get("peso_abajo", 0.7))
    total = peso = 0.0
    for franja, p in (("abajo", peso_abajo), ("arriba", 1.0 - peso_abajo)):
        if a.get(franja) is not None and b.get(franja) is not None:
            d = cv2.compareHist(a[franja], b[franja], cv2.HISTCMP_BHATTACHARYYA)
            total += p * float(d)
            peso += p
    return total / peso if peso else 1.0


def centro_dentro(caja_pose: tuple[int, int, int, int] | None,
                  caja: tuple[int, int, int, int] | None) -> bool:
    """El centro de la caja de MediaPipe cae dentro de la caja de YOLO."""
    if caja_pose is None or caja is None:
        return False
    cx = (caja_pose[0] + caja_pose[2]) / 2.0
    cy = (caja_pose[1] + caja_pose[3]) / 2.0
    return caja[0] <= cx <= caja[2] and caja[1] <= cy <= caja[3]


def mayor(candidatos: list[Persona]) -> Persona:
    """La caja más grande (lo de antes), o no visible."""
    vis = [c for c in candidatos if c.visible and c.caja is not None]
    if not vis:
        return Persona(visible=False)
    return max(vis, key=lambda c: (c.caja[2] - c.caja[0]) * (c.caja[3] - c.caja[1]))


def de_la_pose(candidatos: list[Persona], caja_pose) -> Persona:
    """La caja de YOLO de quien hace el gesto: la que contiene el centro de su
    pose (la más grande si hay varias); si ninguna, la más grande."""
    dentro = [c for c in candidatos if centro_dentro(caja_pose, c.caja)]
    return mayor(dentro) if dentro else mayor(candidatos)


class Identidad:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.activa_cfg = bool(cfg.get("activa", True))
        self.firma: dict | None = None
        self.lateral: float | None = None
        self.t_visto = -1e9
        self.d_ultima: float | None = None     # distancia del elegido (o del mejor rechazado)
        self.descartadas = 0                   # cajas descartadas en el último fotograma

    @property
    def activa(self) -> bool:
        return self.firma is not None

    def fijar(self, imagen: np.ndarray, persona: Persona, ahora: float) -> bool:
        """Guarda la firma de `persona`. False si no se ha podido."""
        if not self.activa_cfg or not persona.visible or persona.caja is None:
            return False
        f = firma(imagen, persona.caja, self.cfg)
        if f["abajo"] is None and f["arriba"] is None:
            return False
        self.firma = f
        self.lateral, self.t_visto = persona.lateral, ahora
        self.d_ultima = 0.0
        return True

    def olvidar(self) -> None:
        self.firma = None
        self.lateral = None
        self.d_ultima = None
        self.descartadas = 0

    def elegir(self, imagen: np.ndarray, candidatos: list[Persona], ahora: float) -> Persona:
        """La persona fijada entre los candidatos, o no visible si no está."""
        if not self.activa:
            return mayor(candidatos)
        c = self.cfg
        umbral = float(c.get("umbral", 0.5))
        reciente = ahora - self.t_visto <= float(c.get("memoria_s", 1.0))
        mejor, mejor_puntos, mejor_d = None, float("inf"), None
        rechazado_d = None
        self.descartadas = 0
        for cand in candidatos:
            if not cand.visible or cand.caja is None:
                continue
            fc = firma(imagen, cand.caja, c)
            d = distancia(self.firma, fc, c)
            salto = (abs(cand.lateral - self.lateral)
                     if reciente and cand.lateral is not None and self.lateral is not None
                     else 0.0)
            if d > umbral or salto > float(c.get("salto_max", 0.5)):
                self.descartadas += 1
                rechazado_d = d if rechazado_d is None else min(rechazado_d, d)
                continue
            puntos = d + float(c.get("peso_posicion", 0.5)) * salto
            if puntos < mejor_puntos:
                mejor, mejor_puntos, mejor_d, mejor_f = cand, puntos, d, fc
        if mejor is None:
            self.d_ultima = rechazado_d
            return Persona(visible=False, ms=candidatos[0].ms if candidatos else 0.0)
        self.d_ultima = mejor_d
        self.lateral, self.t_visto = mejor.lateral, ahora
        self._adaptar(mejor_f, mejor_d)
        return replace(mejor)

    def _adaptar(self, f: dict, d: float) -> None:
        """Muy parecido: la firma se mueve un poco hacia lo visto (cambios de
        luz). Solo si se parece mucho, para no irse hacia otra persona."""
        if d > float(self.cfg.get("adaptar_bajo", 0.3)):
            return
        alfa = float(self.cfg.get("alfa", 0.1))
        for franja in ("arriba", "abajo"):
            nueva = f.get(franja)
            vieja = self.firma.get(franja)
            if nueva is None:
                continue
            if vieja is None:
                continue               # la franja de arriba solo se fija quieto
            mezcla = (1.0 - alfa) * vieja + alfa * nueva
            cv2.normalize(mezcla, mezcla, 1.0, 0.0, cv2.NORM_L1)
            self.firma[franja] = mezcla
