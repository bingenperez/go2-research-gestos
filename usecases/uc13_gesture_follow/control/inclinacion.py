"""usecases/uc13_gesture_follow/control/inclinacion.py

Inclinar el cuerpo hacia arriba (cabeceo) con SportClient.Euler, para que la
cámara vea manos y cabeza cuando el robot está en reposo.

SDK (unitree_sdk2py/go2/sport/sport_client.py, comprobado en la imagen):
    Euler(roll, pitch, yaw)  ->  API 1007, radianes.

Método comprobado a mano en el robot (2026-11-18):
    BalanceStand y después Euler(0, pitch, 0) EN BUCLE cada 0.1 s. Con un
    solo Euler el robot no se mueve. Con pitch +0.25 el morro BAJÓ
    (--probar-inclinacion, 2026-11-20, servicio mcf): el morro sube con pitch
    NEGATIVO (`inclinacion.signo: -1`, pendiente de confirmar).

Solo con TIEMPOS, sin IMU para decidir (en una prueba la IMU dio 0.0; en
otra, con el robot moviéndose, +13 grados: no se depende de ella):

    RECTO ──inclinar()──► INCLINANDO ──t_inclinar_s──► INCLINADO
      ▲                                                    │
      └──── t_enderezar_s ◄── ENDEREZANDO ◄──enderezar()───┘

    INCLINANDO / INCLINADO: Euler(0, signo * max_rad, 0) cada periodo_envio_s
    ENDEREZANDO:            Euler(0, 0, 0)               cada periodo_envio_s
    RECTO:                  no se envía nada

recto_y_estable() solo es True en RECTO, es decir, después de haber mandado
Euler 0 durante t_enderezar_s. Antes de cualquier Move hay que esperar a eso.

Quien use esta clase tiene que haber mandado BalanceStand y no estar
enviando Move. Si `enviar` rechaza la orden (lista blanca) o devuelve un
código distinto de 0, la inclinación se desactiva en la sesión y se endereza.
"""

from __future__ import annotations

from typing import Callable

RECTO, INCLINANDO, INCLINADO, ENDEREZANDO = "RECTO", "INCLINANDO", "INCLINADO", "ENDEREZANDO"


class Inclinacion:
    def __init__(self, cfg: dict, enviar: Callable[[float], int]) -> None:
        self.activa = bool(cfg.get("activa", True))
        self.max_rad = abs(float(cfg["max_rad"]))
        self.signo = 1.0 if float(cfg["signo"]) >= 0 else -1.0
        self.periodo = float(cfg.get("periodo_envio_s", 0.1))
        self.t_inclinar = float(cfg.get("t_inclinar_s", 0.5))
        self.t_enderezar = float(cfg.get("t_enderezar_s", 0.5))

        self.enviar = enviar
        self.estado = RECTO
        self.cmd = 0.0              # pitch enviado (rad, con signo)
        self.t_cambio = 0.0         # entrada al estado actual
        self.t_envio = -1e9
        self.aviso = ""             # mensaje para --show / --debug

    # ------------------------------------------------------------------
    def recto_y_estable(self) -> bool:
        return self.estado == RECTO

    def objetivo(self) -> float:
        """pitch que se manda inclinado (rad, con signo)."""
        return self.signo * self.max_rad

    def inclinar(self, ahora: float) -> None:
        """Desde RECTO: empezar a inclinarse (en reposo y en BalanceStand)."""
        if not self.activa or self.estado != RECTO:
            return
        self._cambiar(INCLINANDO, ahora)
        self.paso(ahora)                      # primer Euler en este mismo ciclo

    def enderezar(self, ahora: float) -> None:
        if self.estado in (RECTO, ENDEREZANDO):
            return
        self._cambiar(ENDEREZANDO, ahora)
        self.paso(ahora)                      # primer Euler 0 en este mismo ciclo

    def desactivar(self, motivo: str, ahora: float) -> None:
        self.activa = False
        self.aviso = f"inclinacion desactivada: {motivo}"
        print(f"\n  [!] {self.aviso}")
        self.enderezar(ahora)

    # ------------------------------------------------------------------
    def paso(self, ahora: float) -> None:
        """Reenvía Euler cada `periodo` y avanza por tiempo."""
        if self.estado == RECTO:
            return
        if self.estado == INCLINANDO and ahora - self.t_cambio >= self.t_inclinar:
            self.estado = INCLINADO
        hecho = self.estado == ENDEREZANDO and ahora - self.t_cambio >= self.t_enderezar

        cmd = 0.0 if self.estado == ENDEREZANDO else self.objetivo()
        if hecho or ahora - self.t_envio >= self.periodo - 1e-6:
            self.t_envio = ahora
            self.cmd = cmd
            try:
                codigo = self.enviar(cmd)
            except ValueError as e:
                # La lista blanca lo ha rechazado (p. ej. cambió la postura).
                if cmd != 0.0:
                    self.desactivar(f"Euler rechazado: {e}", ahora)
                return
            if codigo != 0 and self.activa:
                self.desactivar(f"Euler devolvio {codigo}", ahora)
                return
        if hecho:
            self.estado = RECTO
            self.cmd = 0.0

    def _cambiar(self, estado: str, ahora: float) -> None:
        self.estado = estado
        self.t_cambio = ahora
        self.t_envio = -1e9
