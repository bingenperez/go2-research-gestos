"""usecases/uc13_gesture_follow/control/seguimiento.py

Ley de control del seguimiento. Parte de ControlSeguimiento de uc04
(usecases/uc04_person_following/deploy/follow_person.py, copiada, no
importada), rehecha tras la prueba real del 2026-11-18.

DIAGNÓSTICO QUE LA MOTIVA (experiments/uc13_gesture_follow/2026-11-18T14*)
-------------------------------------------------------------------------
Con kp en unidades de tam (1.4) y tam cambiando ~0.07 por metro, la orden casi
siempre salía por debajo de 0.2 y se subía al mínimo útil: el robot iba a
0.20 m/s el 75-95 % del tiempo andando (la marcha más lenta de Sport Mode:
pasitos cortos y balanceo) y la persona se alejaba. Además cada arranque
pasaba por 0.08 y 0.17, el giro se encendía y apagaba con valores minúsculos y
los valores atípicos de tam pasaban el filtro.

QUÉ HACE AHORA
--------------
    Filtro         mediana (quita atípicos de un fotograma) y/o media
                   exponencial, configurable, sobre tam y lateral.
    Distancia      en METROS: d = d_obj + (tam_obj - tam) / pendiente, con la
                   pendiente sacada de dos puntos calibrados (2.0 m y 1.3 m).
    Avance         histéresis: arranca si d > d_arrancar, para si d < d_parar.
                   En marcha: vx = kp_d * (d - d_obj), entre vx_marcha_min
                   (por encima del mínimo de Sport Mode) y vx_max.
    Marcha atrás   si d < marcha_atras.d_m (1.4 m, con margen sobre la
                   saturación de tam en ~1.29 m): -vx_atras, como mucho max_s seguidos; si
                   sigue cerca, se para y no vuelve a retroceder hasta d > d_rearme.
    Giro           histéresis sobre lateral; |wz| entre wz_min y wz_max.
    Rampas         al acelerar Y al frenar, para vx y para wz. Las paradas de
                   seguridad usan detener(): 0 inmediato.
    Nunca vy.

Convención de signos (punto de vista del ROBOT), igual que uc04:
    persona a la derecha de la imagen (lateral > 0)  ->  wz < 0
    persona lejos (d > objetivo)                      ->  vx > 0
"""

from __future__ import annotations

from collections import deque

import numpy as np


class Filtro:
    """Filtro de una señal que puede faltar (None).

    tipo: "mediana", "ema" o "mediana+ema". Sin dato se devuelve None y la
    ventana se conserva (un hueco corto no reinicia el filtro).
    """

    def __init__(self, tipo: str, ventana: int, alfa: float) -> None:
        if tipo not in ("mediana", "ema", "mediana+ema"):
            raise ValueError(f"filtro desconocido: {tipo}")
        self.tipo, self.alfa = tipo, float(alfa)
        self.buf: deque = deque(maxlen=max(1, int(ventana)))
        self.valor: float | None = None

    def reiniciar(self) -> None:
        self.buf.clear()
        self.valor = None

    def paso(self, x: float | None) -> float | None:
        if x is None:
            return None
        self.buf.append(float(x))
        y = float(np.median(self.buf)) if "mediana" in self.tipo else float(x)
        if "ema" in self.tipo:
            y = y if self.valor is None else self.alfa * y + (1 - self.alfa) * self.valor
        self.valor = y
        return y


class Distancia:
    """tam (alto de la caja de YOLO) -> metros, lineal con dos puntos.

    Calibrado 2026-11-18 con la cámara del robot: 2.0 m -> 0.786, 1.3 m ->
    0.888 (uc04 había medido una recta parecida: 1 m 0.85, 2 m 0.75, 3 m 0.65).
    A 1.3 m la caja ya toca el borde de arriba y tam satura en ~0.89
    (tam_saturacion): más cerca la distancia medida se queda en ~1.29 m. Por
    eso la marcha atrás tiene que saltar ANTES (marcha_atras.d_m), con margen.
    """

    def __init__(self, cfg: dict) -> None:
        self.d_obj = float(cfg["d_objetivo_m"])
        self.tam_obj = float(cfg["tam_objetivo"])
        d_cerca, tam_cerca = float(cfg["d_cerca_m"]), float(cfg["tam_cerca"])
        if abs(self.d_obj - d_cerca) < 1e-6 or tam_cerca <= self.tam_obj:
            raise ValueError("calibracion: tam_cerca debe ser MAYOR que tam_objetivo")
        self.pendiente = (tam_cerca - self.tam_obj) / (self.d_obj - d_cerca)  # tam por metro
        self.tam_sat = float(cfg.get("tam_saturacion", 1.0))
        # Distancia más corta que se puede medir (tam saturado).
        self.d_min = self.metros(self.tam_sat)

    def tam(self, d: float) -> float:
        """Inversa de metros(): el tam que corresponde a d metros."""
        return self.tam_obj + self.pendiente * (self.d_obj - d)

    def metros(self, tam: float | None) -> float | None:
        if tam is None:
            return None
        return max(0.2, self.d_obj + (self.tam_obj - tam) / self.pendiente)


class ControlSeguimiento:
    """Convierte (distancia, lateral) en (vx, 0, wz) con histéresis y rampas."""

    def __init__(self, cfg: dict, limites: dict) -> None:
        self.d_obj = float(cfg["d_objetivo_m"])
        self.d_arrancar = float(cfg["d_arrancar_m"])
        self.d_parar = float(cfg["d_parar_m"])
        self.kp_d = float(cfg["kp_distancia"])
        self.vx_marcha_min = float(cfg["vx_marcha_min"])
        self.vx_max = float(cfg["vx_max"])
        self.acel_vx = float(cfg["acel_vx"])

        a = cfg["marcha_atras"]
        self.atras_on = bool(a["activa"])
        # Umbral de la marcha atrás. d_cerca_m es solo un punto de calibración:
        # está pegado a la saturación de tam, ver Distancia.
        self.d_cerca = float(a.get("d_m", cfg["d_cerca_m"]))
        self.vx_atras = float(a["vx"])
        self.atras_max_s = float(a["max_s"])
        self.d_rearme_atras = float(a["d_rearme_m"])

        self.lat_girar = float(cfg["lat_girar"])
        self.lat_dejar = float(cfg["lat_dejar_girar"])
        self.kp_giro = float(cfg["kp_giro"])
        self.wz_min = float(cfg["wz_min"])
        self.wz_max = float(cfg["wz_max"])
        self.acel_wz = float(cfg["acel_wz"])

        self.vx_lim_min, self.vx_lim_max = limites["vx_range"]
        self.wz_lim_min, self.wz_lim_max = limites["wz_range"]

        self.vx = self.wz = 0.0
        self.en_marcha = False
        self.girando = False
        self.atras = False
        self.t_atras = 0.0
        self.atras_bloqueada = False

    # ------------------------------------------------------------------
    def detener(self) -> tuple[float, float, float]:
        """Velocidad 0 YA, sin rampa (paradas de seguridad)."""
        self.vx = self.wz = 0.0
        self.en_marcha = self.girando = self.atras = False
        return 0.0, 0.0, 0.0

    def quiere_moverse(self) -> bool:
        return self.en_marcha or self.girando or self.atras

    def en_reposo(self) -> bool:
        return self.vx == 0.0 and abs(self.wz) < 0.05 and not self.quiere_moverse()

    # ------------------------------------------------------------------
    def _objetivo_vx(self, d: float | None, ahora: float) -> float:
        if d is None:
            self.en_marcha = self.atras = False
            return 0.0

        # Marcha atrás limitada.
        if self.atras_bloqueada and d > self.d_rearme_atras:
            self.atras_bloqueada = False
        if self.atras_on and d < self.d_cerca and not self.atras_bloqueada:
            self.en_marcha = False
            if not self.atras:
                self.atras, self.t_atras = True, ahora
            if ahora - self.t_atras > self.atras_max_s:
                # Lleva demasiado retrocediendo y sigue cerca: se para.
                self.atras, self.atras_bloqueada = False, True
                return 0.0
            return -self.vx_atras
        if self.atras and d >= self.d_cerca:
            self.atras = False

        # Avance con histéresis.
        if self.en_marcha and d < self.d_parar:
            self.en_marcha = False
        elif not self.en_marcha and d > self.d_arrancar:
            self.en_marcha = True
        if not self.en_marcha:
            return 0.0
        return float(np.clip(self.kp_d * (d - self.d_obj), self.vx_marcha_min, self.vx_max))

    def _objetivo_wz(self, lat: float | None) -> float:
        if lat is None:
            self.girando = False
            return 0.0
        if self.girando and abs(lat) < self.lat_dejar:
            self.girando = False
        elif not self.girando and abs(lat) > self.lat_girar:
            self.girando = True
        if not self.girando:
            return 0.0
        # Persona a la derecha (lat > 0) -> girar a la derecha (wz < 0).
        mag = float(np.clip(self.kp_giro * abs(lat), self.wz_min, self.wz_max))
        return -mag if lat > 0 else mag

    @staticmethod
    def _rampa(actual: float, objetivo: float, paso_max: float) -> float:
        nuevo = actual + float(np.clip(objetivo - actual, -paso_max, paso_max))
        return 0.0 if abs(nuevo) < 1e-3 else nuevo

    def paso(self, d: float | None, lat: float | None, dt: float,
             ahora: float) -> tuple[float, float, float]:
        obj_vx = self._objetivo_vx(d, ahora)
        obj_wz = self._objetivo_wz(lat)
        # Rampa al acelerar y al frenar.
        self.vx = self._rampa(self.vx, obj_vx, self.acel_vx * dt)
        self.wz = self._rampa(self.wz, obj_wz, self.acel_wz * dt)
        # Topes propios y del contrato.
        self.vx = float(np.clip(self.vx, max(-self.vx_atras, self.vx_lim_min),
                                min(self.vx_max, self.vx_lim_max)))
        self.wz = float(np.clip(self.wz, max(-self.wz_max, self.wz_lim_min),
                                min(self.wz_max, self.wz_lim_max)))
        return self.vx, 0.0, self.wz
