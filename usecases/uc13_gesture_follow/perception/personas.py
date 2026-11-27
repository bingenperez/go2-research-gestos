"""usecases/uc13_gesture_follow/perception/personas.py

Detección de la PERSONA para el seguimiento, con YOLO. COPIA adaptada de
usecases/uc04_person_following/perception/detector.py (no se importa).

Por qué YOLO y no MediaPipe para seguir: a 2 m, con la cámara a 30 cm del
suelo, el robot andando solo ve piernas y cadera. MediaPipe (BlazePose)
detecta a la persona usando la cara como referencia, así que sin cabeza no es
fiable. La clase `person` de YOLO se dispara igual con cuerpos parciales (ya
lo usaba uc04 a 2 m). MediaPipe queda para los gestos.

Diferencias con uc04:
    - no abre la cámara: recibe la imagen que ya ha leído DetectorGestos
      (con el arreglo de fotogramas repetidos y el watchdog);
    - no suaviza ni cuenta fallos: eso lo hace control/seguimiento.py;
    - mide lo que tarda (ms), para --debug.

Magnitudes (igual que uc04):
    lateral  centro de la caja respecto al centro de la imagen, [-1, +1]
    tam      alto de la caja / alto de la imagen. Por debajo de ~3 m la
             cabeza se sale por arriba y la caja queda cortada: en la práctica
             mide dónde están los pies. uc04 midió con la cámara del robot:
             1 m -> 0.85, 2 m -> 0.75, 3 m -> 0.65.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Persona:
    visible: bool
    lateral: float | None = None
    tam: float | None = None
    confianza: float = 0.0
    caja: tuple[int, int, int, int] | None = None
    ms: float = 0.0


def elegir(cajas: list[tuple[float, float, float, float, float]], w: int,
           h: int) -> Persona:
    """La caja MÁS GRANDE (la persona más cercana), como uc04.

    cajas: [(x1, y1, x2, y2, confianza)] en píxeles.
    """
    mejor, mejor_area = None, 0.0
    for x1, y1, x2, y2, conf in cajas:
        area = (x2 - x1) * (y2 - y1)
        if area > mejor_area:
            mejor_area, mejor = area, (x1, y1, x2, y2, conf)
    if mejor is None:
        return Persona(visible=False)
    x1, y1, x2, y2, conf = mejor
    return Persona(visible=True,
                   lateral=float(((x1 + x2) / 2.0 - w / 2.0) / (w / 2.0)),
                   tam=float((y2 - y1) / h), confianza=float(conf),
                   caja=(int(x1), int(y1), int(x2), int(y2)))


class DetectorPersonas:
    """YOLO sobre una imagen ya leída."""

    def __init__(self, cfg: dict) -> None:
        from ultralytics import YOLO
        modelo = Path(cfg["modelo"])
        if not modelo.is_absolute():
            modelo = Path(__file__).resolve().parents[3] / modelo
        if not modelo.exists():
            raise RuntimeError(f"No existe el modelo YOLO: {modelo} (ver README de uc13)")
        self.yolo = YOLO(str(modelo))
        self.imgsz = int(cfg.get("imgsz", 320))
        self.confianza_min = float(cfg.get("confianza_min", 0.4))
        self.ms = 0.0

    def detectar(self, imagen: np.ndarray) -> Persona:
        """La caja más grande (como uc04)."""
        todas = self.detectar_todas(imagen)
        if not todas:
            return Persona(visible=False, ms=self.ms)
        p = max(todas, key=lambda c: (c.caja[2] - c.caja[0]) * (c.caja[3] - c.caja[1]))
        return p

    def detectar_todas(self, imagen: np.ndarray) -> list[Persona]:
        """Todas las personas (para seguir solo a una: perception/identidad.py)."""
        t = time.perf_counter()
        h, w = imagen.shape[:2]
        # classes=[0]: solo la clase `person` de COCO, como uc04.
        res = self.yolo.predict(imagen, imgsz=self.imgsz, conf=self.confianza_min,
                                classes=[0], verbose=False)[0]
        cajas = [(*(float(v) for v in c.xyxy[0]), float(c.conf[0])) for c in res.boxes]
        self.ms = (time.perf_counter() - t) * 1000.0
        todas = [elegir([c], w, h) for c in cajas]
        for p in todas:
            p.ms = self.ms
        return todas
