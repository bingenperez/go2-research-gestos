#!/usr/bin/env python3
"""usecases/uc13_gesture_follow/deploy/gesture_follow.py

uc13 — Gestos + seguimiento a 2 m, con inclinación para ver los gestos.

    cámara ─┬─► YOLO ─────────► persona (lateral, tam) ─► control ─► Move(vx, 0, wz)
            └─► MediaPipe Pose ─► gesto ─► confirmación ─► acción Sport Mode
    reposo:  BalanceStand + Euler(0, signo * 0.25, 0) cada 0.1 s (morro arriba)

GESTOS Y ESTADOS (nada más)
---------------------------
    gesto        TUMBADO          DE_PIE quieto           SIGUIENDO / PAUSADO
    una mano     StandUp+Balance  nada                    nada
    saludo       StandUp+Balance  Hello                   nada
    dos manos    nada             empieza a SEGUIR        PARA
    T            nada             Hello                   PARA (respaldo)
    cruzados     nada             StopMove+StandDown      nada
El saludo (mano oscilando, perception/pose.py DetectorSaludo) se confirma con
su propia ventana, sin N fotogramas, y tiene prioridad sobre "una mano".
    PARA = Move(0,0,0) + StopMove + BalanceStand, y queda quieto de pie.
SENTADO / DUDOSO / SIN_DATOS: ningún gesto hace nada.

Llamadas permitidas: Move (solo SIGUIENDO/PAUSADO, cuerpo recto, vy = 0,
-vx_atras <= vx <= 0.4), Euler (solo en reposo y en BalanceStand, roll = yaw =
0, |pitch| <= max_rad), StopMove, BalanceStand, StandUp, StandDown, Hello y
RiseSit (solo en el cierre seguro). Nunca Sit ni acrobacias.

POR QUÉ ASÍ (prueba real del 2026-11-18, ver README)
----------------------------------------------------
    - A 2 m y con la cámara a 30 cm del suelo, andando solo ve piernas: la
      persona se sigue con YOLO (copiado de uc04). MediaPipe, para gestos.
    - En reposo (siguiendo parado > 1 s, o quieto de pie) el cuerpo se
      inclina 0.25 rad hacia arriba para ver las manos a menos de 2 m:
      BalanceStand y Euler reenviado cada 0.1 s (un solo Euler no lo mueve).
      Solo tiempos para decidir: 1 s tras BalanceStand antes del primer
      Euler, 0.5 s para inclinarse y 0.7 s de Euler 0 antes de cualquier Move
      (medido 2026-11-20: recto en ~0.4 s). signo -1 (pitch negativo = morro
      arriba). Inclinado
      no se calcula la distancia: si la persona se mueve, se endereza y se
      vuelve a medir.
    - Control en metros con histéresis, rampas al acelerar y al frenar,
      velocidad mínima en marcha por encima de la de Sport Mode, giro con
      histéresis y marcha atrás limitada (control/seguimiento.py).

INDEPENDIENTE
-------------
Copia lo que necesita de uc12 (pose, estado, fotogramas repetidos,
confirmador, cierre) y de uc04 (YOLO, ley de control). No importa nada de
usecases/uc04*, uc09*, uc10*, uc11* ni uc12*. Solo go2core y tools/robot_camera.py.

SEGURIDAD
---------
    - confirmación escribiendo "si" antes de mover el robot real
    - gesto = N fotogramas seguidos. Re-armado por gesto: el gesto que acaba
      de actuar tiene que dejar de verse. Otro gesto confirmado durante el
      cooldown queda pendiente y se ejecuta al acabar si se sigue viendo.
      PARAR no espera al cooldown
    - watchdog: sin imagen nueva en 0.5 s o estado viejo -> Move(0,0,0) en ese
      ciclo (si se movía) y no se confirman gestos
    - siguiendo: velocidad 0 en cada ciclo sin orden válida mientras no está
      en reposo en BalanceStand; en reposo (BalanceStand) no se manda Move
    - cierre: Euler 0 (0.7 s), si seguía Move(0,0,0) + StopMove, y el
      cierre seguro de uc12 (RiseSit antes si sentado/dudoso; sin datos, nada)
    - --dry-run no crea SportClient: solo imprime lo que haría
    - la parada de emergencia real sigue siendo el mando: L2 + B

Uso (dentro de la caja, con MediaPipe instalado; ver README):
    python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --iface <IFACE> --estado
    python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \\
        --mode real --iface <IFACE> --source robot --dry-run --show --debug
    python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \\
        --mode real --iface <IFACE> --probar-inclinacion [--mantener 15 --ciclos 2 --log]
    python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \\
        --mode real --iface <IFACE> --source robot --show --duration 180 --log
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse   # noqa: E402
import csv        # noqa: E402
import json       # noqa: E402
import math       # noqa: E402
import signal     # noqa: E402
import statistics  # noqa: E402
import sys        # noqa: E402
import threading  # noqa: E402
import time       # noqa: E402
from collections import Counter, deque  # noqa: E402
from datetime import datetime    # noqa: E402
from pathlib import Path         # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from go2core import paths                              # noqa: E402
from go2core.control import contract as ct              # noqa: E402
from go2core.logging.run import git_dirty, git_sha      # noqa: E402
from control.inclinacion import (ENDEREZANDO, INCLINADO, RECTO,  # noqa: E402
                                 Inclinacion)
from control.seguimiento import ControlSeguimiento, Distancia, Filtro  # noqa: E402
from perception.personas import Persona                 # noqa: E402
from perception.identidad import (Identidad, centro_dentro, de_la_pose,  # noqa: E402
                                  mayor)
from perception.pose import (CRUZADOS, DOS, MANO, NINGUNO, SALUDO, T,  # noqa: E402
                             DetectorSaludo, MedidaSaludo, Pose)

UC = Path(__file__).resolve().parents[1]

DE_PIE, TUMBADO, SENTADO = "DE_PIE", "TUMBADO", "SENTADO"
DUDOSO, SIN_DATOS = "DUDOSO", "SIN_DATOS"
QUIETO, SIGUIENDO, PAUSADO = "QUIETO", "SIGUIENDO", "PAUSADO"

PARAR = {"flag": False}


def dormir(s: float, interrumpible: bool = True) -> bool:
    """Espera s segundos. Devuelve False si Ctrl-C la ha cortado."""
    fin = time.monotonic() + s
    while time.monotonic() < fin:
        if interrumpible and PARAR["flag"]:
            return False
        time.sleep(0.02)
    return True


# ===========================================================================
# Estado del robot (copia de uc12 + IMU)
# ===========================================================================
def _clasificar_por_mode(mode: int, altura: float, cfg: dict) -> str:
    """Solo con usar_mode: true."""
    if mode in cfg.get("modos_sentado", []):
        return SENTADO
    de_pie_m = mode in cfg["modos_de_pie"]
    tumbado_m = mode in cfg["modos_tumbado"]
    alto = altura > float(cfg["altura_umbral"])
    if de_pie_m and alto:
        return DE_PIE
    if tumbado_m and not alto:
        return TUMBADO
    if not de_pie_m and not tumbado_m and not alto:
        return TUMBADO
    return DUDOSO


def clasificar_postura(mode: int, altura: float, cfg: dict) -> str:
    """body_height (o mode) -> DE_PIE / SENTADO / TUMBADO / DUDOSO."""
    if cfg.get("usar_mode", "rangos_altura" not in cfg):
        return _clasificar_por_mode(mode, altura, cfg)
    for clave, postura in (("tumbado", TUMBADO), ("sentado", SENTADO),
                           ("de_pie", DE_PIE)):
        lo, hi = cfg["rangos_altura"][clave]
        if float(lo) <= altura <= float(hi):
            return postura
    return DUDOSO


def pitch_de_cuaternion(q) -> float:
    """Cabeceo (rad) de un cuaternión (w, x, y, z)."""
    w, x, y, z = (float(v) for v in q)
    return math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))


class EstadoSport:
    """Suscripción a rt/sportmodestate. Solo lectura.

    El cabeceo de la IMU (imu_state.rpy[1], radianes) es SOLO DIAGNÓSTICO:
    nada se decide con él. En las primeras pruebas daba 0.0 porque el robot
    no se movía; en las de 2026-11-20 (A y B) marca -13.4 grados inclinado,
    igual que rt/lowstate. escuchar_lowstate() calcula el cabeceo del
    cuaternión de rt/lowstate (como uc05) y --estado / --probar-inclinacion
    muestran los dos.
    """

    def __init__(self, cfg: dict, max_edad_s: float) -> None:
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import SportModeState_
        self.cfg = cfg
        self.max_edad_s = max_edad_s
        self._lock = threading.Lock()
        self.mode: int | None = None
        self.altura: float | None = None
        self.pitch: float | None = None
        self.pitch_low: float | None = None
        self._t = 0.0
        self._t_low = 0.0
        self.n = 0
        self.sub = ChannelSubscriber("rt/sportmodestate", SportModeState_)
        self.sub.Init(self._on, 10)
        self.sub_low = None

    def _on(self, m) -> None:
        with self._lock:
            self.mode = int(m.mode)
            self.altura = float(m.body_height)
            try:
                self.pitch = float(m.imu_state.rpy[1])
            except Exception:
                self.pitch = None
            self._t = time.monotonic()
            self.n += 1

    def escuchar_lowstate(self) -> None:
        """Solo diagnóstico (--estado, --probar-inclinacion): rt/lowstate."""
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_
        self.sub_low = ChannelSubscriber("rt/lowstate", LowState_)
        self.sub_low.Init(self._on_low, 10)

    def _on_low(self, m) -> None:
        try:
            p = pitch_de_cuaternion(m.imu_state.quaternion)
        except Exception:
            return
        with self._lock:
            self.pitch_low, self._t_low = p, time.monotonic()

    def leer_pitch_low(self) -> float | None:
        with self._lock:
            if not self._t_low or time.monotonic() - self._t_low > self.max_edad_s:
                return None
            return self.pitch_low

    def leer(self) -> tuple[int | None, float | None, float]:
        with self._lock:
            edad = time.monotonic() - self._t if self._t else float("inf")
            return self.mode, self.altura, edad

    def leer_pitch(self) -> float | None:
        with self._lock:
            if not self._t or time.monotonic() - self._t > self.max_edad_s:
                return None
            return self.pitch

    def estado(self) -> str:
        mode, altura, edad = self.leer()
        if mode is None or altura is None or edad > self.max_edad_s:
            return SIN_DATOS
        return clasificar_postura(mode, altura, self.cfg)

    def esperar_primero(self, timeout: float) -> bool:
        fin = time.monotonic() + timeout
        while time.monotonic() < fin:
            if self.n > 0:
                return True
            time.sleep(0.05)
        return False


class EstadoSimulado:
    """Estado inventado: solo --dry-run con la webcam y sin robot."""

    def __init__(self, inicial: str) -> None:
        self.valor = inicial
        self.n = 1

    def leer(self) -> tuple[None, None, float]:
        return None, None, 0.0

    def leer_pitch(self) -> None:
        return None

    def leer_pitch_low(self) -> None:
        return None

    def estado(self) -> str:
        return self.valor

    def esperar_primero(self, timeout: float) -> bool:
        return True


# ===========================================================================
# Salidas
# ===========================================================================
# Llamadas por nombre. Move y Euler NO están aquí: van por mover() y euler(),
# que comprueban sus límites.
PERMITIDAS = {"StandUp", "BalanceStand", "StopMove", "StandDown", "RiseSit", "Hello"}


def _comprobar_move(vx: float, vy: float, wz: float, lim: dict) -> None:
    """Última barrera antes de Move: nunca lateral; topes y marcha atrás."""
    if vy != 0.0:
        raise ValueError(f"Move con velocidad lateral vy={vy}")
    if not (-float(lim["vx_atras"]) - 1e-9 <= vx <= float(lim["vx_max"]) + 1e-9):
        raise ValueError(f"Move fuera de limites vx={vx}")
    if abs(wz) > float(lim["wz_max"]) + 1e-9:
        raise ValueError(f"Move fuera de limites wz={wz}")


def _comprobar_euler(roll: float, pitch: float, yaw: float, lim: dict) -> None:
    """Euler solo para mirar arriba: roll = yaw = 0 y |pitch| <= max."""
    if roll != 0.0 or yaw != 0.0:
        raise ValueError(f"Euler con roll/yaw distinto de 0: {roll}, {yaw}")
    if abs(pitch) > float(lim["pitch_max_rad"]) + 1e-9:
        raise ValueError(f"Euler fuera de limites pitch={pitch:.3f} rad")


# Códigos de error RPC del SDK (unitree_sdk2py/rpc/internal.py, en la imagen).
CODIGOS_RPC = {
    0: "ok (aceptado)",
    3001: "error desconocido",
    3102: "el cliente no pudo enviar",
    3103: "API no registrada en el cliente",
    3104: "SIN RESPUESTA del robot (timeout)",
    3105: "respuesta que no corresponde",
    3106: "datos de respuesta erroneos",
    3107: "lease no valido",
    3201: "el robot no pudo responder",
    3202: "error interno del robot",
    3203: "el robot NO IMPLEMENTA esta API (firmware / servicio)",
    3204: "parametros rechazados por el robot",
    3205: "lease denegado (otro cliente tiene el control)",
    3206: "lease inexistente",
    3207: "lease ya existe",
}


def texto_codigo(codigo: int) -> str:
    return f"{codigo} = {CODIGOS_RPC.get(codigo, 'codigo no documentado en el SDK')}"


def crear_motion_switcher():
    """MotionSwitcherClient (solo lectura: CheckMode). Las pruebas lo sustituyen."""
    from unitree_sdk2py.comm.motion_switcher.motion_switcher_client import MotionSwitcherClient
    ms = MotionSwitcherClient()
    ms.SetTimeout(3.0)
    ms.Init()
    return ms


class SalidaRobot:
    """SportClient DIRECTO, como uc04/uc12. Lista blanca: nunca Sit."""

    nombre = "robot real (SportClient)"

    def __init__(self, limites: dict, timeout_s: float) -> None:
        from unitree_sdk2py.go2.sport.sport_client import SportClient
        self.lim = limites
        self.sport = SportClient()
        self.sport.SetTimeout(float(timeout_s))
        self.sport.Init()

    def llamar(self, nombre: str) -> int:
        if nombre not in PERMITIDAS:
            raise ValueError(f"accion no permitida en uc13: {nombre}")
        print(f"  -> SportClient.{nombre}()")
        return int(getattr(self.sport, nombre)() or 0)

    def mover(self, vx: float, vy: float, wz: float) -> int:
        _comprobar_move(vx, vy, wz, self.lim)
        return int(self.sport.Move(float(vx), 0.0, float(wz)) or 0)

    def euler(self, roll: float, pitch: float, yaw: float) -> int:
        _comprobar_euler(roll, pitch, yaw, self.lim)
        return int(self.sport.Euler(0.0, float(pitch), 0.0) or 0)

    def diagnostico(self) -> list[str]:
        """Solo lectura: versión del API de sport y servicio de movimiento
        activo (MotionSwitcher.CheckMode). No cambia nada en el robot."""
        lineas = []
        try:
            lineas.append(f"SDK sport API (cliente): {self.sport.GetApiVersion()}")
        except Exception as e:
            lineas.append(f"SDK sport API (cliente): error {e}")
        try:
            codigo, ver = self.sport.GetServerApiVersion()
            lineas.append(f"sport API del ROBOT: {ver}  (codigo {texto_codigo(int(codigo))})")
        except Exception as e:
            lineas.append(f"sport API del ROBOT: error {e}")
        try:
            codigo, datos = crear_motion_switcher().CheckMode()
            lineas.append(f"MotionSwitcher.CheckMode: {datos}  (codigo {texto_codigo(int(codigo))})")
        except Exception as e:
            lineas.append(f"MotionSwitcher.CheckMode: error {e}")
        return lineas


class SalidaNula:
    """--dry-run: no crea SportClient ni envía nada."""

    nombre = "DRY RUN (no se envia nada)"

    def __init__(self, limites: dict) -> None:
        self.lim = limites
        self._ultimo: tuple[float, float] | None = None
        self._ultimo_euler: float | None = None
        self.n_move = 0
        self.n_euler = 0

    def llamar(self, nombre: str) -> int:
        if nombre not in PERMITIDAS:
            raise ValueError(f"accion no permitida en uc13: {nombre}")
        print(f"  [DRY RUN] haria: SportClient.{nombre}()")
        return 0

    def diagnostico(self) -> list[str]:
        return ["DRY RUN: no se consulta el robot"]

    def mover(self, vx: float, vy: float, wz: float) -> int:
        _comprobar_move(vx, vy, wz, self.lim)
        self.n_move += 1
        cmd = (round(vx, 2), round(wz, 2))
        if cmd != self._ultimo:
            print(f"  [DRY RUN] haria: SportClient.Move({vx:.2f}, 0.00, {wz:.2f})")
            self._ultimo = cmd
        return 0

    def euler(self, roll: float, pitch: float, yaw: float) -> int:
        _comprobar_euler(roll, pitch, yaw, self.lim)
        self.n_euler += 1
        p = round(pitch, 3)
        if p != self._ultimo_euler:
            print(f"  [DRY RUN] haria: SportClient.Euler(0, {pitch:+.3f}, 0) cada 0.1 s "
                  f"({math.degrees(pitch):+.0f} grados)")
            self._ultimo_euler = p
        return 0


class Abortar(Exception):
    """Corta una acción a medias. El mensaje es el resultado que se registra."""


class Robot:
    """Acciones, modo de seguimiento, inclinación y cierre."""

    def __init__(self, salida, estado, cfg_acc: dict, cfg_estado: dict,
                 cfg_incl: dict, dry_run: bool) -> None:
        self.salida = salida
        self.estado = estado
        self.c = cfg_acc
        self.ce = cfg_estado
        self.ci = cfg_incl
        self.simulado = isinstance(estado, EstadoSimulado)
        self.virtual = dry_run and not self.simulado
        rangos = cfg_estado.get("rangos_altura")
        self.suelo = (float(rangos["tumbado"][1]) if rangos
                      else float(cfg_estado["altura_umbral"]))
        self.llamadas: list[str] = []
        self.modo = QUIETO
        self.vx = self.wz = 0.0           # último Move enviado
        self.t_move_no_cero = -1e9        # último Move distinto de 0
        self.en_balance = False           # tras BalanceStand y sin Move desde entonces
        self.t_balance_quieto = -1e9      # último BalanceStand para inclinarse quieto
        self.t_balance = -1e9             # último BalanceStand (cualquiera)
        self.incl = Inclinacion(cfg_incl, enviar=self._enviar_euler)

    # -- postura -------------------------------------------------------
    def activo(self) -> bool:
        return self.modo in (SIGUIENDO, PAUSADO) or self.incl.estado != RECTO

    def postura(self) -> str:
        """Postura; siguiendo, pausado o inclinado vale el rango de_pie_activo."""
        p = self.estado.estado()
        if p in (SENTADO, DUDOSO) and self.activo() and "de_pie_activo" in self.ce:
            _, h, _ = self.estado.leer()
            lo, hi = self.ce["de_pie_activo"]
            if h is not None and float(lo) <= h <= float(hi):
                return DE_PIE
        return p

    def siguiendo(self) -> bool:
        return self.modo in (SIGUIENDO, PAUSADO)

    # -- Move y Euler --------------------------------------------------
    def mover(self, vx: float, wz: float) -> int:
        """Move(vx, 0, wz). SOLO siguiendo y con el cuerpo RECTO y estable."""
        if not self.siguiendo():
            raise ValueError(f"Move fuera de SIGUIENDO (modo {self.modo})")
        if not self.incl.recto_y_estable():
            raise ValueError(f"Move con el cuerpo {self.incl.estado}")
        codigo = self.salida.mover(vx, 0.0, wz)
        self.vx, self.wz = vx, wz
        if vx != 0.0 or wz != 0.0:
            self.t_move_no_cero = time.monotonic()
            self.en_balance = False
        return codigo

    def puede_inclinarse(self, ahora: float) -> bool:
        """En reposo: vx = 0 y wz ~ 0 desde hace espera_quieto_s, de pie, en
        BalanceStand (sin Move desde entonces) y al menos espera_balance_s
        desde el último BalanceStand. Esa espera (1 s) es la de
        --probar-inclinacion, la única condición que ha funcionado en el robot
        (2026-11-20); el primer Euler justo tras BalanceStand no se ha probado."""
        return (self.incl.activa and self.en_balance and self.postura() == DE_PIE
                and self.vx == 0.0 and abs(self.wz) < 0.05
                and ahora - self.t_move_no_cero >= float(self.ci["espera_quieto_s"])
                and ahora - self.t_balance >= float(self.ci.get("espera_balance_s", 1.0)))

    def balance_quieto(self, ahora: float) -> None:
        """QUIETO de pie sin BalanceStand (p. ej. lo levantó el mando antes de
        arrancar): BalanceStand para poder inclinarse. Como mucho cada 5 s."""
        if (self.modo != QUIETO or self.en_balance or not self.incl.activa
                or not self.incl.recto_y_estable() or ahora - self.t_balance_quieto < 5.0
                or self.postura() != DE_PIE):
            return
        self.t_balance_quieto = ahora
        try:
            self._llamar("BalanceStand")
        except Abortar as e:
            print(f"  [!] BalanceStand para inclinarse: {e}")

    def _enviar_euler(self, pitch: float) -> int:
        """Lo llama Inclinacion. Enderezar (0) siempre se permite."""
        if pitch != 0.0:
            if not self.en_balance:
                raise ValueError("Euler sin BalanceStand")
            if self.vx != 0.0 or self.wz != 0.0:
                raise ValueError("Euler con el robot en movimiento")
            if self.postura() != DE_PIE:
                raise ValueError(f"Euler con postura {self.postura()}")
        return self.salida.euler(0.0, pitch, 0.0)

    def enderezar(self) -> bool:
        """Bloqueante: Euler 0 cada 0.1 s durante t_enderezar_s. No se corta
        con Ctrl-C. Solo tiempos, sin IMU."""
        if self.incl.recto_y_estable():
            return True
        print("  enderezando (Euler 0)...")
        ahora = time.monotonic()
        self.incl.enderezar(ahora)
        fin = ahora + self.incl.t_enderezar + 1.0
        while time.monotonic() < fin:
            self.incl.paso(time.monotonic())
            if self.incl.recto_y_estable():
                return True
            time.sleep(0.02)
        print("  [!] no ha terminado de enderezarse")
        return False

    # -- piezas (copia de uc12) ----------------------------------------
    def _llamar(self, nombre: str, requiere: set[str] | None = None) -> None:
        if requiere is not None and not self.virtual:
            p = self.postura()
            if p not in requiere:
                raise Abortar(f"incompatible:{nombre}_con_{p}")
        codigo = self.salida.llamar(nombre)
        self.llamadas.append(nombre)
        if codigo != 0:
            raise Abortar(f"error:{nombre}_codigo_{codigo}")
        if nombre == "BalanceStand":
            self.en_balance = True
            self.t_balance = time.monotonic()
        elif nombre in ("StandDown", "StandUp", "RiseSit", "Hello"):
            self.en_balance = False

    def _dormir(self, clave: str, interrumpible: bool = True) -> None:
        if not dormir(float(self.c[clave]), interrumpible):
            raise Abortar("interrumpido")

    def _esperar_estado(self, esperado: str, durante: float, timeout: float,
                        interrumpible: bool = True) -> None:
        if self.simulado:
            self.estado.valor = esperado
            return
        if self.virtual:
            return
        fin = time.monotonic() + timeout
        t_ok = None
        while time.monotonic() < fin:
            if interrumpible and PARAR["flag"]:
                raise Abortar("interrumpido")
            if self.postura() == esperado:
                t_ok = t_ok or time.monotonic()
                if time.monotonic() - t_ok >= durante:
                    return
            else:
                t_ok = None
            time.sleep(0.05)
        raise Abortar(f"no_{esperado.lower()}")

    def _esperar_alto(self) -> None:
        if self.simulado or self.virtual:
            return
        fin = time.monotonic() + float(self.c["timeout_alto_s"])
        while time.monotonic() < fin:
            if PARAR["flag"]:
                raise Abortar("interrumpido")
            _, altura, _ = self.estado.leer()
            if altura is not None and altura > self.suelo:
                return
            time.sleep(0.05)
        raise Abortar("no_se_levanto")

    # -- acciones ------------------------------------------------------
    def levantar(self) -> str:
        """TUMBADO -> StandUp + BalanceStand, y esperar DE_PIE estable."""
        self._llamar("StandUp", {TUMBADO})
        self._dormir("espera_standup_s")
        self._esperar_alto()
        self._llamar("BalanceStand")
        self._dormir("espera_balance_s")
        self._esperar_estado(DE_PIE, float(self.c["estable_s"]),
                             float(self.c["timeout_estable_s"]))
        return "ok"

    def tumbar(self, forzar: bool = False) -> str:
        """Euler 0, y StopMove + StandDown SOLO con DE_PIE confirmado (uc12)."""
        self.enderezar()
        p = self.postura()
        if p == TUMBADO:
            return "ya_tumbado"
        if p == SIN_DATOS:
            return "sin_estado_usa_el_mando"
        if p in (SENTADO, DUDOSO):
            if not forzar:
                return f"incompatible:tumbar_con_{p}"
            print(f"  {p.lower()}: primero RiseSit y esperar a que este de pie")
            self._llamar("RiseSit")
            try:
                self._esperar_estado(DE_PIE, float(self.c["estable_s"]),
                                     float(self.c["timeout_estable_s"]),
                                     interrumpible=False)
            except Abortar:
                return "no_de_pie_tras_risesit_usa_el_mando"
        self._llamar("StopMove", {DE_PIE})
        self._dormir("espera_stopmove_s", interrumpible=not forzar)
        self._llamar("StandDown", {DE_PIE})
        self._esperar_estado(TUMBADO, 0.0, float(self.c["timeout_standdown_s"]),
                             interrumpible=not forzar)
        return "ok"

    def saludar(self) -> str:
        """Euler 0, Hello, esperar, comprobar DE_PIE y BalanceStand (para poder
        volver a inclinarse y ver los gestos)."""
        self.enderezar()
        self._llamar("Hello", {DE_PIE})
        self._dormir("espera_hello_s", interrumpible=False)
        try:
            self._esperar_estado(DE_PIE, float(self.c["estable_s"]),
                                 float(self.c["timeout_estable_s"]),
                                 interrumpible=False)
        except Abortar:
            return "no_de_pie_tras_hello"
        self._llamar("BalanceStand")
        return "ok"

    def seguir(self) -> str:
        """DE_PIE quieto -> SIGUIENDO. No envía nada (puede seguir inclinado
        hasta que la persona se mueva)."""
        if not self.virtual and self.postura() != DE_PIE:
            raise Abortar(f"incompatible:seguir_con_{self.postura()}")
        self.modo = SIGUIENDO
        return "ok"

    def balance(self) -> None:
        """En reposo siguiendo: StopMove + BalanceStand (para poder inclinarse)."""
        self._llamar("StopMove")
        dormir(float(self.c["espera_stopmove_s"]), interrumpible=False)
        self._llamar("BalanceStand")

    def parar_seguimiento(self) -> str:
        """Move(0,0,0) + StopMove + BalanceStand, y modo QUIETO.

        Inclinado, el robot ya está en reposo en BalanceStand: no hace falta
        (ni se puede) mandar Move; solo se pasa a QUIETO.
        """
        if not self.siguiendo():
            return "no_seguia"
        if not self.incl.recto_y_estable():
            self.modo = QUIETO
            return "ok_ya_en_reposo"
        try:
            self.mover(0.0, 0.0)
        except Exception as e:
            print(f"  [!] Move(0,0,0) fallo: {e}")
        self.modo = QUIETO
        self.llamadas.append("Move0")
        self._llamar("StopMove")
        self._dormir("espera_stopmove_s", interrumpible=False)
        p = self.postura()
        if p != DE_PIE and not self.virtual:
            return f"parado_sin_balance_{p.lower()}"
        self._llamar("BalanceStand")
        return "ok"


# ===========================================================================
# Máquina de estados gesto -> acción
# ===========================================================================
class Confirmador:
    """Un gesto cuenta si se mantiene N fotogramas frescos seguidos.

    Re-armado POR GESTO: tras actuar, ESE gesto tiene que dejar de verse un
    fotograma antes de volver a contar (mantener "dos manos" no rebota). Los
    demás gestos cuentan con normalidad, también durante el cooldown: si se
    confirman entonces, el bucle los deja pendientes hasta que acabe.

    SALUDO no cuenta fotogramas: ya viene confirmado por su ventana de
    oscilación. Se devuelve en cuanto llega (fresco y re-armado).
    """

    def __init__(self, frames: dict, rearmar: bool) -> None:
        self.frames = {g: int(frames[g]) for g in (DOS, MANO, CRUZADOS, T)}
        self.rearmar = rearmar
        self.previo = NINGUNO
        self.contador = 0
        self.bloqueado: str | None = None

    @property
    def armado(self) -> bool:
        return self.bloqueado is None or self.previo != self.bloqueado

    def reiniciar(self) -> None:
        self.previo, self.contador = NINGUNO, 0

    def paso(self, gesto: str, fresca: bool) -> str | None:
        if not fresca:
            self.reiniciar()      # watchdog: una imagen vieja rompe la racha
            return None
        if gesto != self.previo:
            self.previo, self.contador = gesto, 0
        if self.bloqueado is not None and gesto != self.bloqueado:
            self.bloqueado = None
        if gesto == NINGUNO:
            return None
        self.contador += 1
        if gesto == self.bloqueado:
            return None
        if gesto == SALUDO:
            return gesto
        return gesto if self.contador >= self.frames[gesto] else None

    def consumido(self, gesto: str) -> None:
        self.contador = 0
        if self.rearmar:
            self.bloqueado = gesto

    def necesarios(self, gesto: str) -> int:
        return 1 if gesto == SALUDO else self.frames.get(gesto, 0)


def decidir(gesto: str, postura: str, modo: str) -> tuple[str, str]:
    """(accion, motivo). accion "nada" significa que no se hace nada."""
    if modo in (SIGUIENDO, PAUSADO):
        if gesto in (DOS, T):
            return "parar", ""
        return "nada", "siguiendo_para_primero"
    if postura not in (TUMBADO, DE_PIE):
        return "nada", f"estado_{postura.lower()}"
    if gesto == SALUDO:
        return ("saludar", "") if postura == DE_PIE else ("levantar", "")
    if gesto == MANO:
        return ("levantar", "") if postura == TUMBADO else ("nada", "ya_de_pie")
    if gesto == DOS:
        return ("seguir", "") if postura == DE_PIE else ("nada", "tumbado_levantate_primero")
    if gesto == T:
        return ("saludar", "") if postura == DE_PIE else ("nada", "saludo_solo_de_pie")
    if gesto == CRUZADOS:
        return ("tumbar", "") if postura == DE_PIE else ("nada", "ya_tumbado")
    return "nada", "sin_gesto"


def ejecutar(accion: str, robot: Robot) -> str:
    funciones = {"saludar": robot.saludar, "tumbar": robot.tumbar,
                 "seguir": robot.seguir, "parar": robot.parar_seguimiento}
    if accion in funciones:
        try:
            return funciones[accion]()
        except Abortar as e:
            return str(e)
    if accion == "levantar":
        try:
            return robot.levantar()
        except Abortar as e:
            r = str(e)
            if r == "interrumpido" or r.startswith(("incompatible:StandUp",
                                                    "error:StandUp")):
                return r
            print(f"  [!] {r}: no llego a estar estable, aplico el cierre seguro")
            try:
                r2 = robot.tumbar(forzar=True)
            except Abortar as e2:
                r2 = str(e2)
            return f"{r}+tumbar:{r2}"
    raise ValueError(accion)


MENSAJES = {
    "ya_de_pie": "ya esta de pie",
    "ya_tumbado": "ya esta tumbado",
    "saludo_solo_de_pie": "esta tumbado: solo saluda de pie (levantalo con una mano)",
    "tumbado_levantate_primero": "esta tumbado: para seguir, primero levantalo (una mano)",
    "siguiendo_para_primero": "esta siguiendo: para primero (dos manos o T)",
    "no_de_pie_tras_hello": "tras Hello no se confirma de pie: no hago nada mas",
    "ok_ya_en_reposo": "ok (ya estaba en reposo e inclinado)",
    "sin_estado_usa_el_mando": "sin estado del robot: no envio nada. USA EL MANDO (L2+B)",
    "no_de_pie_tras_risesit_usa_el_mando": ("tras RiseSit no se confirma de pie: "
                                            "no envio StandDown. USA EL MANDO (L2+B)"),
}


# ===========================================================================
# Seguimiento: un paso por ciclo del bucle
# ===========================================================================
class Seguidor:
    """Decide la velocidad de cada ciclo en SIGUIENDO / PAUSADO.

    Paradas de seguridad (watchdog, pérdida, postura) SIN rampa. Con el cuerpo
    inclinado no hay Move: si la persona se mueve, se endereza y se vuelve a
    medir recto.
    """

    def __init__(self, robot: Robot, cfg: dict, limites: dict, cfg_incl: dict) -> None:
        self.robot = robot
        self.c = cfg
        self.ci = cfg_incl
        self.control = ControlSeguimiento(cfg, limites)
        self.dist = Distancia(cfg)
        tipo, ventana, alfa = cfg["filtro"], int(cfg["ventana"]), float(cfg["alfa"])
        self.f_tam = Filtro(tipo, ventana, alfa)
        self.f_lat = Filtro(tipo, ventana, alfa)
        self.f_tam_inc = Filtro("mediana", ventana, 1.0)
        self.f_lat_inc = Filtro("mediana", ventana, 1.0)
        self.periodo = 1.0 / float(cfg.get("move_hz", 20.0))
        self.t_persona = 0.0
        self.t_no_de_pie: float | None = None
        self.t_move = -1e9
        self.t_prev = time.monotonic()
        self.t_reposo: float | None = None
        self.t_inclinado: float | None = None
        self.ref_tam: float | None = None
        self.estaba_recto = True
        # Tras enderezarse para volver a medir, no se deja inclinar otra vez
        # hasta que el control haya podido decidir (si no, se inclinaría en
        # el mismo ciclo y el robot oscilaría sin llegar a andar).
        self.para_medir = False
        self.t_no_inclinar = 0.0
        self.lateral: float | None = None
        self.tam: float | None = None
        self.d: float | None = None
        self.motivo = ""

    def permite_inclinar(self, ahora: float) -> bool:
        return self.control.en_reposo() and ahora >= self.t_no_inclinar

    def empezar(self, ahora: float) -> None:
        self.control.detener()
        for f in (self.f_tam, self.f_lat, self.f_tam_inc, self.f_lat_inc):
            f.reiniciar()
        self.t_persona = ahora
        self.t_no_de_pie = None
        self.t_prev = ahora
        self.t_move = -1e9
        self.t_reposo = None
        self.t_inclinado = None
        self.ref_tam = None
        # Al empezar a seguir hay que medir la distancia RECTO antes de
        # inclinarse: no se inclina hasta espera_quieto_s después. (Sin esto,
        # si el gesto se confirma durante la espera de espera_balance_s, aún
        # recto, se inclinaba sin haber medido y no retrocedía con la persona
        # cerca.) Si ya está inclinado: enderezar y medir.
        self.t_no_inclinar = ahora + float(self.ci["espera_quieto_s"])
        if not self.robot.incl.recto_y_estable():
            self.robot.incl.enderezar(ahora)
            self.para_medir = True

    def _enviar(self, vx: float, wz: float, ahora: float, forzar: bool = False) -> str:
        """Move limitado a move_hz. Pasar a cero se envía al momento."""
        r = self.robot
        if not r.incl.recto_y_estable():
            return ""                                 # inclinado: en reposo, sin Move
        if vx == 0.0 and wz == 0.0 and r.vx == 0.0 and r.wz == 0.0 and r.en_balance:
            return ""                                 # reposo en BalanceStand: sin Move
        a_cero = vx == 0.0 and wz == 0.0 and (r.vx or r.wz)
        if not (forzar or a_cero) and ahora - self.t_move < self.periodo:
            return ""
        self.t_move = ahora
        codigo = r.mover(vx, wz)
        return "" if codigo == 0 else f"error:Move_codigo_{codigo}"

    def parada(self, ahora: float, motivo: str) -> str:
        """Velocidad 0 inmediata (watchdog, pérdida, postura)."""
        self.motivo = motivo
        self.control.detener()
        return self._enviar(0.0, 0.0, ahora, forzar=True)

    def paso(self, ahora: float, sano: bool, persona: Persona | None, nueva: bool,
             postura: str) -> str:
        """Un ciclo. Devuelve "" o el motivo para DEJAR de seguir."""
        r = self.robot
        if not r.siguiendo():
            return ""
        if not sano:
            res = self.parada(ahora, "watchdog")
            if res:
                return res
            if ahora - self.t_persona > float(self.c["pausa_max_s"]):
                return "watchdog_prolongado"
            return ""

        if postura != DE_PIE and not r.virtual:
            self.t_no_de_pie = self.t_no_de_pie or ahora
            res = self.parada(ahora, f"postura_{postura.lower()}")
            if res:
                return res
            if ahora - self.t_no_de_pie > float(self.c["no_de_pie_max_s"]):
                return f"no_de_pie_{postura.lower()}"
            return ""
        self.t_no_de_pie = None

        recto = r.incl.recto_y_estable()
        if recto and not self.estaba_recto:
            # Vuelve a recto: lo medido inclinado no vale para el control.
            self.f_tam.reiniciar()
            self.f_lat.reiniciar()
            self.t_prev = ahora
            if self.para_medir:
                self.para_medir = False
                self.t_no_inclinar = ahora + float(self.ci["espera_quieto_s"])
        self.estaba_recto = recto

        if nueva:
            if persona is not None and persona.visible:
                self.t_persona = ahora
                if recto:
                    self.lateral = self.f_lat.paso(persona.lateral)
                    self.tam = self.f_tam.paso(persona.tam)
                else:
                    self.lateral = self.f_lat_inc.paso(persona.lateral)
                    self.tam = self.f_tam_inc.paso(persona.tam)
            else:
                self.lateral = self.tam = None

        perdida = ahora - self.t_persona
        if perdida > float(self.c["perdida_s"]):
            if r.modo == SIGUIENDO:
                print(f"\n  persona perdida {perdida:.1f} s: velocidad 0, PAUSADO")
                r.modo = PAUSADO
                for f in (self.f_tam, self.f_lat):
                    f.reiniciar()
            res = self.parada(ahora, "persona_perdida")
            if res:
                return res
            if perdida > float(self.c["pausa_max_s"]):
                return "persona_perdida"
            return ""

        if r.modo == PAUSADO:
            print("\n  persona recuperada: SIGUIENDO")
            r.modo = SIGUIENDO
            self.t_prev = ahora

        # --- inclinado: sin control de distancia ------------------------
        if not recto:
            self.control.detener()
            self.d = None
            if r.incl.estado == INCLINADO:
                self.t_inclinado = self.t_inclinado or ahora
                if (self.ref_tam is None and self.tam is not None
                        and ahora - self.t_inclinado >= 0.5):
                    self.ref_tam = self.tam
                elif self.ref_tam is not None and self.tam is not None:
                    se_mueve = abs(self.tam - self.ref_tam) > float(self.ci["banda_tam"])
                    lado = self.lateral is not None and abs(self.lateral) > float(self.c["lat_girar"])
                    if se_mueve or lado:
                        print(f"\n  la persona se mueve (tam {self.ref_tam:.2f} -> "
                              f"{self.tam:.2f}, lat {_f(self.lateral, '+.2f')}): enderezo")
                        r.incl.enderezar(ahora)
                        self.para_medir = True
            else:
                # Subiendo más o enderezando: la referencia ya no vale.
                self.t_inclinado = None
                self.ref_tam = None
            self.motivo = "inclinado" if r.incl.estado != ENDEREZANDO else "enderezando"
            return ""
        self.t_inclinado = None
        self.ref_tam = None

        # --- recto: control -------------------------------------------
        if nueva:
            dt = min(ahora - self.t_prev, 0.2)
            self.t_prev = ahora
            self.d = self.dist.metros(self.tam)
            vx, _, wz = self.control.paso(self.d, self.lateral, dt, ahora)
        else:
            vx, wz = self.control.vx, self.control.wz
        self.motivo = ("marcha atras" if self.control.atras else
                       "" if (vx or wz) else "parado")

        # Reposo prolongado: StopMove + BalanceStand para poder inclinarse.
        if self.control.en_reposo() and r.vx == 0.0 and r.wz == 0.0 and not r.en_balance:
            self.t_reposo = self.t_reposo or ahora
            if ahora - self.t_reposo >= float(self.ci["espera_quieto_s"]):
                r.balance()
                self.t_reposo = None
                return ""
        else:
            self.t_reposo = None
        return self._enviar(vx, wz, ahora)


# ===========================================================================
def modo_estado(estado: EstadoSport, segundos: float = 10.0) -> int:
    """--estado: imprime lo que llega de rt/sportmodestate. No mueve nada."""
    print("leyendo rt/sportmodestate (solo lectura). Pon el robot tumbado,")
    print("sentado y de pie con el mando y comprueba la clasificacion.\n")
    if not estado.esperar_primero(3.0):
        print("No llega rt/sportmodestate. Revisa la red (GUIA 4.2) y la app del movil.")
        return 1
    _escuchar_lowstate(estado)
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < segundos:
            mode, h, edad = estado.leer()
            print(f"\r  mode {mode:>3}  body_height {h:6.3f} m  pitch {_pitchs(estado)}  edad "
                  f"{edad * 1000:5.0f} ms  ->  {estado.estado():<9}", end="", flush=True)
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    print(f"\n\n{estado.n} mensajes. Si la clasificacion no cuadra, ajusta "
          f"`rangos_altura` en configs/gesture_follow.yaml.")
    return 0


def _escuchar_lowstate(estado) -> None:
    if hasattr(estado, "escuchar_lowstate") and estado.sub_low is None:
        try:
            estado.escuchar_lowstate()
        except Exception as e:
            print(f"  (sin rt/lowstate para comparar la IMU: {e})")


def _pitchs(estado) -> str:
    """Cabeceo de las dos fuentes, en grados (solo diagnóstico)."""
    def g(p):
        return _f(None if p is None else math.degrees(p), '+5.1f')
    low = getattr(estado, "leer_pitch_low", lambda: None)()
    return f"sportmodestate {g(estado.leer_pitch())} / lowstate {g(low)} grados"


UMBRAL_MOV_GRADOS = 2.0     # |IMU - base| por encima: "se está moviendo"
MOVIDO_GRADOS = 3.0         # desviación máxima mínima para decir "se ha movido"


def _metricas_ciclo(serie: list[dict], ciclo: int, fuente: str, t_end: float) -> dict:
    """Métricas de un ciclo a partir de la serie de la IMU (grados).

    base: media de los 0.3 s antes del primer Euler inclinado del ciclo.
    latencia: desde el primer Euler inclinado hasta |IMU - base| > umbral.
    t90: hasta el 90 % de la desviación máxima.
    vuelta: desde el primer Euler 0 del ciclo hasta |IMU - base| < umbral.
    """
    inc = [x for x in serie if x["ciclo"] == ciclo and x["fase"] == "inclinado"]
    ende = [x for x in serie if x["ciclo"] == ciclo and x["fase"] == "enderezando"]
    m = {"ciclo": ciclo, "fuente": fuente, "movido": False}
    if not inc or fuente == "ninguna":
        m["motivo"] = "sin IMU"
        return m
    t_ini = inc[0]["t"]
    m["t_inicio_desde_balance_s"] = round(t_ini, 3)
    previas = [x[fuente] for x in serie if t_ini - 0.3 <= x["t"] < t_ini and x[fuente] is not None]
    if not previas:
        previas = [x[fuente] for x in inc[:3] if x[fuente] is not None]
    if not previas:
        m["motivo"] = "sin IMU"
        return m
    base = statistics.mean(previas)
    dev = [(x["t"], x[fuente] - base) for x in inc if x[fuente] is not None]
    if not dev:
        m["motivo"] = "sin IMU"
        return m
    t_max, d_max = max(dev, key=lambda v: abs(v[1]))
    m["base_grados"] = round(base, 2)
    m["desviacion_max_grados"] = round(d_max, 2)
    m["duracion_inclinado_s"] = round(inc[-1]["t"] - t_ini, 2)
    m["movido"] = abs(d_max) >= MOVIDO_GRADOS
    if not m["movido"]:
        return m
    t_mov = next(t for t, d in dev if abs(d) > UMBRAL_MOV_GRADOS)
    t_90 = next(t for t, d in dev if abs(d) >= 0.9 * abs(d_max))
    m["latencia_s"] = round(t_mov - t_ini, 3)
    m["empieza_desde_balance_s"] = round(t_mov, 3)
    m["t90_s"] = round(t_90 - t_ini, 3)
    if ende:
        t_e = ende[0]["t"]
        m["duracion_enderezando_s"] = round(ende[-1]["t"] - t_e, 2)
        vuelta = next((x["t"] for x in ende if x[fuente] is not None
                       and abs(x[fuente] - base) < UMBRAL_MOV_GRADOS), None)
        m["vuelta_s"] = None if vuelta is None else round(vuelta - t_e, 3)
        m["vuelta_ok"] = vuelta is not None and vuelta - t_e <= t_end
    return m


def _texto_metricas(m: dict, t_end: float) -> list[str]:
    c = m["ciclo"]
    if m.get("motivo") == "sin IMU":
        return [f"ciclo {c}: sin datos de la IMU: no se puede medir"]
    if not m["movido"]:
        return [f"ciclo {c}: NO se ha movido (max {m['desviacion_max_grados']:+.1f} grados "
                f"en {m['duracion_inclinado_s']:.1f} s inclinado; empezo a "
                f"{m['t_inicio_desde_balance_s']:.1f} s del BalanceStand)"]
    d = m["desviacion_max_grados"]
    lado = "morro ARRIBA" if d < 0 else "morro ABAJO"
    lineas = [f"ciclo {c}: SE HA MOVIDO {d:+.1f} grados ({lado}, segun las pruebas del "
              f"2026-11-20: IMU negativa = morro arriba)",
              f"         empieza a moverse {m['latencia_s']:.2f} s despues del primer Euler "
              f"({m['empieza_desde_balance_s']:.1f} s despues del BalanceStand); 90 % a "
              f"{m['t90_s']:.2f} s"]
    if "vuelta_s" in m:
        if m["vuelta_s"] is None:
            lineas.append(f"         NO vuelve a < {UMBRAL_MOV_GRADOS:.0f} grados en "
                          f"{m['duracion_enderezando_s']:.1f} s de Euler 0")
        else:
            ok = ("OK" if m["vuelta_ok"] else
                  "NO: tarda MAS que t_enderezar_s; el bucle mandaria Move aun inclinado")
            lineas.append(f"         vuelve a < {UMBRAL_MOV_GRADOS:.0f} grados {m['vuelta_s']:.2f} s "
                          f"despues del primer Euler 0 (t_enderezar_s = {t_end}: {ok})")
    return lineas


def probar_inclinacion(salida, estado, ci: dict, acc: dict, dry_run: bool,
                       mantener_s: float = 3.0, espera_balance_s: float = 1.0,
                       ciclos: int = 1, log: bool = False) -> int:
    """--probar-inclinacion: robot DE PIE. El MISMO método que el bucle:
    BalanceStand y Euler(0, signo * max_rad, 0) cada periodo_envio_s. Funciona
    aunque `inclinacion.activa` sea false.

        BalanceStand ─ espera_balance_s (sin Euler) ─ Euler 0 0.3 s ─┐
        ┌────────────────────────────────────────────────────────────┘
        └► ciclo k = 1..ciclos (SIN nuevo BalanceStand entre ciclos):
             Euler(signo*max_rad) durante t_inclinar_s + mantener_s
             Euler 0 durante max(t_enderezar_s, 2 s)

    Diagnóstico de solo lectura (versión del API de sport, MotionSwitcher.
    CheckMode, mode, body_height) y, de cada llamada, el código de respuesta
    y la duración.

    Medida en el tiempo (2026-11-20: la primera secuencia tras encender o
    tras un rato no movía nada y la siguiente sí): la IMU de rt/lowstate (si
    no llega, la de sportmodestate) se muestrea a ~50 Hz. Por ciclo: cuándo
    empieza a moverse (latencia desde el primer Euler y desde el
    BalanceStand), cuándo llega al 90 % y cuánto tarda en volver a recto tras
    Euler 0, comparado con t_enderezar_s. La IMU solo MIDE: no decide nada.
    Con --log guarda la serie (serie.csv) y las métricas (manifest.json).
    """
    if estado.estado() != DE_PIE:
        print(f"El robot tiene que estar DE PIE (ahora {estado.estado()}). "
              "Ponlo de pie con el mando o con el gesto de una mano.")
        return 1
    _escuchar_lowstate(estado)
    signo = 1.0 if float(ci["signo"]) >= 0 else -1.0
    pitch = signo * abs(float(ci["max_rad"]))
    periodo = float(ci.get("periodo_envio_s", 0.1))
    t_inc = float(ci.get("t_inclinar_s", 0.5))
    t_end = float(ci.get("t_enderezar_s", 0.5))
    enderezar_s = max(t_end, 2.0)       # largo para poder medir la vuelta
    ciclos = max(1, int(ciclos))
    leer_low = getattr(estado, "leer_pitch_low", lambda: None)

    print("\n== diagnostico (solo lectura) ==")
    diag = getattr(salida, "diagnostico", lambda: ["(sin diagnostico)"])()
    for linea in diag:
        print(f"  {linea}")
    mode0, h0, _ = estado.leer()
    print(f"  antes: mode {mode0}  body_height {_f(h0, '.3f')} m  ({estado.estado()})")

    duracion = espera_balance_s + 0.3 + ciclos * (t_inc + mantener_s + enderezar_s)
    print(f"\nBalanceStand; espera {espera_balance_s} s; Euler 0 0.3 s; y {ciclos} "
          f"ciclo(s) de Euler(0, {pitch:+.3f}, 0) cada {periodo} s durante "
          f"{t_inc + mantener_s:.1f} s + Euler(0, 0, 0) durante {enderezar_s:.1f} s, "
          f"sin nuevo BalanceStand entre ciclos (~{duracion:.0f} s en total).")
    t_bs = time.monotonic()
    codigo_bs = salida.llamar("BalanceStand")
    dur_bs = time.monotonic() - t_bs
    print(f"  BalanceStand: codigo {texto_codigo(codigo_bs)}, {dur_bs * 1000:.0f} ms")

    serie: list[dict] = []
    alturas = []
    envios: dict[str, list[tuple[int, float]]] = {}   # fase -> [(codigo, s)]

    def muestrear(seg: float, fase: str, ciclo: int, p_cmd: float | None) -> None:
        """Envía Euler(p_cmd) cada periodo (si p_cmd no es None) y muestrea la
        IMU en cada vuelta (~50 Hz)."""
        fin = time.monotonic() + seg
        t_envio = -1e9
        clave = fase if ciclo == 0 else f"{fase} c{ciclo}"
        while True:
            ahora = time.monotonic()
            codigo = ""
            if p_cmd is not None and ahora - t_envio >= periodo - 1e-6:
                t_envio = ahora
                try:
                    codigo = int(salida.euler(0.0, p_cmd, 0.0))
                except ValueError:
                    raise
                except Exception as e:                 # p. ej. error del SDK
                    print(f"\n  [!] Euler lanzo {type(e).__name__}: {e}")
                    codigo = -1
                dur = time.monotonic() - ahora
                lista = envios.setdefault(clave, [])
                lista.append((codigo, dur))
                if codigo != 0 and sum(1 for c, _ in lista if c == codigo) == 1:
                    print(f"\n  [!] Euler({p_cmd:+.3f}) devolvio {texto_codigo(codigo)} "
                          f"({dur * 1000:.0f} ms)")
            pl, ps = leer_low(), estado.leer_pitch()
            _, h, _ = estado.leer()
            if h is not None:
                alturas.append(h)
            serie.append({"t": time.monotonic() - t_bs, "fase": fase, "ciclo": ciclo,
                          "cmd": "" if p_cmd is None else p_cmd, "codigo": codigo,
                          "lowstate": None if pl is None else math.degrees(pl),
                          "sportmodestate": None if ps is None else math.degrees(ps),
                          "body_height": h})
            if time.monotonic() >= fin:
                break
            print(f"\r  {clave:<15} IMU {_pitchs(estado)}   body_height "
                  f"{_f(h, '.3f')} m   ", end="", flush=True)
            time.sleep(0.02)

    try:
        muestrear(espera_balance_s, "espera", 0, None)
        muestrear(0.3, "recto", 0, 0.0)
        for k in range(1, ciclos + 1):
            try:
                muestrear(t_inc + mantener_s, "inclinado", k, pitch)
            finally:
                muestrear(enderezar_s, "enderezando", k, 0.0)
    finally:
        print()
    mode2, h2, _ = estado.leer()

    hay_low = any(x["lowstate"] is not None for x in serie)
    hay_sport = any(x["sportmodestate"] is not None for x in serie)
    fuente = "lowstate" if hay_low else "sportmodestate" if hay_sport else "ninguna"
    metricas = [_metricas_ciclo(serie, k, fuente, t_end) for k in range(1, ciclos + 1)]

    print("\n== resultado ==")
    print(f"  mode: antes {mode0}, al final {mode2}")
    print(f"  BalanceStand: {texto_codigo(codigo_bs)}")
    todos = []
    for clave, lista in envios.items():
        todos += lista
        cods = Counter(c for c, _ in lista)
        durs = [d for _, d in lista]
        print(f"  Euler {clave:<15}: {len(lista)} enviados; codigos "
              + ", ".join(f"{texto_codigo(c)} x{n}" for c, n in cods.most_common())
              + f"; duracion media {statistics.mean(durs) * 1000:.0f} ms, "
              f"max {max(durs) * 1000:.0f} ms")
    if alturas:
        print(f"  body_height entre {min(alturas):.3f} y {max(alturas):.3f} m")
    print(f"\n  IMU usada para medir: {fuente} (solo mide; no decide nada)")
    for m in metricas:
        for linea in _texto_metricas(m, t_end):
            print(f"  {linea}")

    movidos = [m["movido"] for m in metricas]
    print()
    if codigo_bs != 0:
        print(f"  -> BalanceStand NO fue aceptado ({texto_codigo(codigo_bs)}): Euler "
              "no puede actuar sin el. Revisa esto primero.")
    malos = Counter(c for c, _ in todos if c != 0)
    if malos:
        c, _ = malos.most_common(1)[0]
        print(f"  -> El robot RECHAZA o no responde a Euler ({texto_codigo(c)}).")
        if c == 3203:
            print("     Su firmware / servicio de movimiento no implementa Euler: "
                  "mira la linea MotionSwitcher.CheckMode de arriba.")
        elif c == 3104:
            print("     No hay respuesta: revisa la red y que el servicio de sport "
                  "este activo (CheckMode).")
    elif todos:
        print("  -> Euler ACEPTADO (codigo 0) en todos los envios.")
    if fuente != "ninguna" and len(movidos) > 1 and not movidos[0] and any(movidos[1:]):
        print("  -> PATRON: el ciclo 1 NO se movio y uno posterior SI, sin nuevo "
              "BalanceStand. Mira en que segundo desde el BalanceStand empezo a moverse.")
    elif fuente != "ninguna" and movidos and movidos[0]:
        m = metricas[0]
        print(f"  -> El ciclo 1 SI se movio: empezo {m['latencia_s']:.2f} s despues del "
              f"primer Euler ({m['empieza_desde_balance_s']:.1f} s despues del BalanceStand).")
    elif fuente != "ninguna" and not any(movidos):
        print("  -> Ningun ciclo se ha movido.")
    if any(m.get("movido") and m.get("vuelta_ok") is False for m in metricas):
        print(f"  -> OJO: al menos un ciclo tarda en volver a recto mas que t_enderezar_s "
              f"({t_end} s).")

    if log:
        _guardar_prueba_inclinacion(serie, metricas, {
            "dry_run": dry_run, "pitch": pitch, "signo": signo,
            "max_rad": float(ci["max_rad"]), "periodo_envio_s": periodo,
            "t_inclinar_s": t_inc, "t_enderezar_s": t_end, "mantener_s": mantener_s,
            "espera_balance_s": espera_balance_s, "enderezar_medida_s": enderezar_s,
            "ciclos": ciclos, "diagnostico": diag, "mode_antes": mode0,
            "mode_despues": mode2, "body_height_antes": h0,
            "balancestand_codigo": codigo_bs, "fuente_imu": fuente,
            "codigos_euler": {k: dict(Counter(c for c, _ in v)) for k, v in envios.items()},
            "body_height_min": min(alturas) if alturas else None,
            "body_height_max": max(alturas) if alturas else None,
        })
    if dry_run:
        print("\nDRY RUN: no se ha enviado nada.")
        return 0
    r = input("\n¿El morro (la camara) ha SUBIDO, mirando hacia arriba? [s/n]: ").strip().lower()
    if r.startswith("s"):
        print(f"Bien: `inclinacion.signo: {signo:+.0f}` es el correcto.")
    else:
        print(f"Si ha BAJADO, cambia `inclinacion.signo` a {-signo:+.0f} en "
              "configs/gesture_follow.yaml y repite. Si no se ha movido, guarda "
              "toda la salida (diagnostico y resultado).")
    return 0


def _guardar_prueba_inclinacion(serie: list[dict], metricas: list[dict], extra: dict) -> None:
    sha = git_sha()
    destino = (paths.EXPERIMENTS / "uc13_gesture_follow"
               / f"{datetime.now():%Y-%m-%dT%H%M%S}_{sha}_probar_inclinacion")
    destino.mkdir(parents=True, exist_ok=True)
    cols = ["t", "fase", "ciclo", "cmd", "codigo", "lowstate", "sportmodestate", "body_height"]
    with open(destino / "serie.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["t_desde_balance_s", "fase", "ciclo", "pitch_cmd_rad", "codigo_euler",
                    "pitch_lowstate_grados", "pitch_sportmodestate_grados", "body_height"])
        for x in serie:
            w.writerow(["" if x[c] is None else
                        (f"{x[c]:.4f}" if isinstance(x[c], float) else x[c]) for c in cols])
    (destino / "manifest.json").write_text(json.dumps({
        "usecase": "uc13_gesture_follow",
        "prueba": "probar_inclinacion",
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "git_sha": sha,
        "git": {"sha": git_sha(short=False), "dirty": git_dirty()},
        **extra,
        "metricas": metricas,
    }, indent=2, ensure_ascii=False, default=str))
    print(f"\n  escrito: {destino}")


# Fábricas: las pruebas las sustituyen por un estado, una salida o unos
# detectores falsos.
def crear_estado(necesita_dds: bool, domain: int, iface: str, ce: dict, seg: dict):
    if necesita_dds:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        ChannelFactoryInitialize(domain, iface)
        return EstadoSport(ce, float(seg["estado_max_edad_s"]))
    estado = EstadoSimulado(ce.get("simulado_inicial", TUMBADO))
    print(f"  sin robot: estado SIMULADO, empieza {estado.valor}\n")
    return estado


def crear_salida(mueve: bool, limites: dict, seg: dict):
    return SalidaRobot(limites, float(seg["timeout_sport_s"])) if mueve else SalidaNula(limites)


def crear_personas(cfg: dict):
    from perception.personas import DetectorPersonas
    return DetectorPersonas(cfg["yolo"])


def main() -> int:
    import yaml

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["real"], default="real",
                   help="solo robot real: unitree_mujoco no emula Sport Mode")
    p.add_argument("--iface", default=None)
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--source", choices=["webcam", "robot"], default="webcam",
                   help="webcam del portatil, o camara frontal del robot")
    p.add_argument("--config", default=str(UC / "configs" / "gesture_follow.yaml"))
    p.add_argument("--show", action="store_true",
                   help="ventana con persona, esqueleto, gesto, estado, velocidades y distancia")
    p.add_argument("--duration", type=float, default=300.0)
    p.add_argument("--dry-run", action="store_true",
                   help="detecta, decide y calcula, pero NO crea SportClient ni envia nada")
    p.add_argument("--log", action="store_true", help="registrar el episodio")
    p.add_argument("--debug", action="store_true",
                   help="una linea por fotograma, y [cal] con la mediana de yolo_tam cada 2 s")
    p.add_argument("--estado", action="store_true",
                   help="solo lectura: muestra rt/sportmodestate 10 s y sale")
    p.add_argument("--probar-inclinacion", action="store_true",
                   help="robot de pie: BalanceStand + Euler max_rad cada 0.1 s, vuelve a 0 (signo)")
    p.add_argument("--mantener", type=float, default=3.0,
                   help="con --probar-inclinacion: segundos inclinado por ciclo (ademas de "
                        "t_inclinar_s)")
    p.add_argument("--espera-balance", type=float, default=None,
                   help="con --probar-inclinacion: espera tras BalanceStand antes del primer "
                        "Euler (por defecto inclinacion.espera_balance_s, la del bucle)")
    p.add_argument("--ciclos", type=int, default=1,
                   help="con --probar-inclinacion: ciclos inclinar/enderezar sin nuevo BalanceStand")
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    c = ct.load_contract(paths.CONTRACT)
    dds = c["dds"][args.mode]
    iface = args.iface or paths.iface() or dds["interface"]
    domain = paths.domain() if paths.domain() is not None else dds["domain_id"]
    seg, acc, ce = cfg["seguridad"], cfg["acciones"], cfg["estado_robot"]
    cs, ci = dict(cfg["seguimiento"]), dict(cfg["inclinacion"])
    limites = {"vx_max": cs["vx_max"], "wz_max": cs["wz_max"],
               "vx_atras": cs["marcha_atras"]["vx"] if cs["marcha_atras"]["activa"] else 0.0,
               "pitch_max_rad": abs(float(ci["max_rad"]))}

    mueve = not args.dry_run and not args.estado
    necesita_dds = args.estado or mueve or args.source == "robot" or args.probar_inclinacion

    print("uc13 — gestos + seguimiento a 2 m")
    print(f"  modo    : {args.mode} (domain {domain}, {iface})")
    print(f"  fuente  : {args.source}")
    dist = Distancia(cs)
    d_atras = float(cs["marcha_atras"].get("d_m", cs["d_cerca_m"]))
    print(f"  objetivo: {cs['d_objetivo_m']} m (tam {cs['tam_objetivo']}); "
          f"calibrado {cs['d_cerca_m']} m (tam {cs['tam_cerca']}); "
          f"marcha atras < {d_atras} m (tam {dist.tam(d_atras):.3f}); "
          f"tam satura en {dist.tam_sat} (~{dist.d_min:.2f} m)")
    if cs["marcha_atras"]["activa"] and dist.tam_sat - dist.tam(d_atras) < 0.01:
        print(f"  AVISO: la marcha atras salta a tam {dist.tam(d_atras):.3f}, a menos de 0.01 "
              f"de la saturacion ({dist.tam_sat}): puede no saltar nunca. Sube marcha_atras.d_m.")
    print(f"  inclinacion: {'si' if ci.get('activa', True) else 'no'}, Euler(0, "
          f"{ci['signo']} * {ci['max_rad']} rad, 0) cada {ci.get('periodo_envio_s', 0.1)} s "
          f"en reposo")
    print(f"  ventana : {'si' if args.show else 'no'}")
    if args.dry_run:
        print("  DRY RUN: no se crea SportClient ni se envia nada")
    print()

    estado = crear_estado(necesita_dds, domain, iface, ce, seg)

    if args.estado:
        return modo_estado(estado)

    if mueve:
        print("  ROBOT FISICO. Confirma:")
        print("   - docs/SAFETY.md leido")
        print("   - espacio libre: 3 m alrededor y el recorrido del seguimiento despejado")
        print("   - mando en la mano (L2+B para el robot)")
        print("   - estado comprobado con --estado (tumbado/sentado/de pie)")
        print("   - tam calibrado a 2.0 y 1.3 m; signo de la inclinacion comprobado")
        if input("\n  Escribe 'si' para continuar: ").strip().lower() != "si":
            return 1

    if args.probar_inclinacion:
        if not estado.esperar_primero(2.0):
            print("No llega rt/sportmodestate.")
            return 1
        salida = crear_salida(mueve, limites, seg)
        try:
            return probar_inclinacion(salida, estado, ci, acc, args.dry_run,
                                      mantener_s=args.mantener,
                                      espera_balance_s=(args.espera_balance
                                                        if args.espera_balance is not None
                                                        else float(ci.get("espera_balance_s", 1.0))),
                                      ciclos=args.ciclos, log=args.log)
        finally:
            try:
                for _ in range(3):               # por si se corto a medias
                    salida.euler(0.0, 0.0, 0.0)
                    time.sleep(float(ci.get("periodo_envio_s", 0.1)))
            except Exception as e:
                print(f"  [!] Euler 0 fallo: {e}. USA EL MANDO: L2+B")

    print("cargando YOLO y MediaPipe Pose...")
    from perception.pose import DetectorGestos
    det = DetectorGestos(cfg, camara=args.camera, fuente=args.source,
                         iface=iface, domain=domain, init_dds=False)
    personas = crear_personas(cfg)
    print("detectores listos")

    if necesita_dds and not estado.esperar_primero(2.0):
        if mueve:
            print("No llega rt/sportmodestate: sin estado no se mueve el robot.")
            det.cerrar()
            return 1
        print("[!] No llega rt/sportmodestate: SIN_DATOS, no se haria nada.")

    signal.signal(signal.SIGINT, lambda *_: PARAR.__setitem__("flag", True))

    robot = None
    conf = Confirmador(cfg["gestos"]["frames_necesarios"], bool(acc.get("rearmar", True)))
    # Seguir solo a quien hizo las dos manos (perception/identidad.py).
    identidad = Identidad(cfg.get("identidad") or {"activa": False})
    saludo = DetectorSaludo(cfg["gestos"])
    cs_sal = cfg["gestos"]["saludo"]
    med_sal = MedidaSaludo()
    filas: list = []
    lanzadas: Counter = Counter()
    ignoradas: Counter = Counter()
    n = n_persona = n_watchdog = 0
    t0 = time.monotonic()
    codigo = 0
    max_img = float(seg["imagen_max_edad_s"])
    max_est = float(seg["estado_max_edad_s"])
    cada_n = max(1, int(cfg.get("mediapipe_cada_n_andando", 1)))
    cal = deque(maxlen=30)

    if args.show:
        import cv2

    try:
        salida = crear_salida(mueve, limites, seg)
        robot = Robot(salida, estado, acc, ce, ci, args.dry_run)
        seguidor = Seguidor(robot, cs, c["commands"], ci)
        print(f"salida: {salida.nombre}")
        print(f"estado inicial: {robot.postura()}")
        print("\nen marcha. Una mano = levantarse | dos manos = seguir / parar |")
        print("T = saludar (quieto) o parar (siguiendo) | cruzados = tumbarse |")
        print(f"saludar con la mano = Hello (quieto) o levantarse (tumbado)"
              f"{'' if saludo.activo else ' [DESACTIVADO en el yaml]'}.")
        print("q o Ctrl-C para parar.\n")

        t0 = time.monotonic()
        t_ultima_img = t0
        t_cal = t0
        sin_nueva: Counter = Counter()
        fin_cooldown = 0.0
        pendiente: str | None = None
        ultima = "-"
        pose = Pose(visible=False)
        persona = Persona(visible=False)
        candidatos: list[Persona] = []
        imagen_vista = None
        hueco = 0.0
        n_img = 0
        ms_mp = 0.0
        while not PARAR["flag"] and time.monotonic() - t0 < args.duration:
            imagen = det.leer_imagen()
            ahora = time.monotonic()
            nueva = imagen is not None
            if nueva:
                n_img += 1
                hueco = ahora - t_ultima_img
                t_ultima_img = ahora
                candidatos = _candidatos(personas, imagen)
                if robot.siguiendo() and identidad.activa:
                    # Solo la persona fijada; si no está, no visible (se para).
                    persona = identidad.elegir(imagen, candidatos, ahora)
                else:
                    persona = mayor(candidatos)
                persona.ms = getattr(personas, "ms", persona.ms)
                # Solo se salta MediaPipe siguiendo Y andando. En QUIETO (y
                # siguiendo parado) en TODOS los fotogramas: el saludo es una
                # ventana de tiempo y necesita todas las muestras.
                andando = robot.siguiendo() and (robot.vx != 0.0 or robot.wz != 0.0)
                if andando and n_img % cada_n:
                    pose = Pose(visible=False)
                    med_sal = MedidaSaludo()
                else:
                    tm = time.perf_counter()
                    det.preferir_caja = (persona.caja if identidad.activa and persona.visible
                                         else None)
                    pose = det.detectar(imagen)
                    ms_mp = (time.perf_counter() - tm) * 1000.0
                    med_sal = saludo.paso(ahora, pose.puntos if pose.visible else None)
                    if med_sal.detectado:
                        pose.gesto = SALUDO      # prioridad sobre el gesto del fotograma
                    # Siguiendo: solo cuentan los gestos de la persona fijada.
                    if (identidad.activa and pose.visible
                            and not (persona.visible and centro_dentro(pose.caja, persona.caja))):
                        pose.gesto = NINGUNO
                imagen_vista = imagen
                # Calibración de tam: solo con el cuerpo recto (inclinado no vale).
                if persona.visible and persona.tam is not None and robot.incl.recto_y_estable():
                    cal.append(persona.tam)
            else:
                sin_nueva[det.motivo_sin_imagen] += 1
                if ahora - t_ultima_img > float(seg["sin_imagen_salir_s"]):
                    print("\nla camara ha dejado de dar imagen")
                    break

            # ---- watchdog: imagen y estado ----------------------------
            _, _, edad_est = estado.leer()
            img_ok = (time.monotonic() - t_ultima_img) <= max_img
            est_ok = edad_est <= max_est
            sano = img_ok and est_ok
            if not sano:
                if args.debug and (conf.contador or robot.siguiendo()):
                    print(f"[dbg] {ahora - t0:7.3f} WATCHDOG imagen "
                          f"{ahora - t_ultima_img:.2f} s, estado {edad_est:.2f} s"
                          f" -> contador a 0" + (", velocidad 0" if robot.siguiendo() else ""))
                conf.reiniciar()
                saludo.reiniciar()           # una imagen vieja rompe también la ventana
                pendiente = None
                n_watchdog += 1
            postura = robot.postura()

            # ---- inclinación: en reposo, Euler reenviado cada 0.1 s ------
            # Siguiendo parado (vx = 0, wz ~ 0 más de 1 s) o QUIETO de pie.
            if sano and not robot.siguiendo():
                robot.balance_quieto(ahora)
            if (sano and robot.incl.recto_y_estable() and robot.puede_inclinarse(ahora)
                    and (not robot.siguiendo() or seguidor.permite_inclinar(ahora))):
                robot.incl.inclinar(ahora)
            robot.incl.paso(ahora)

            # ---- seguimiento (velocidad de este ciclo) ----------------
            r_seg = seguidor.paso(ahora, sano, persona, nueva, postura)
            if r_seg:
                print(f"\n  deja de seguir: {r_seg}")
                rr = robot.parar_seguimiento()
                print(f"  resultado: {MENSAJES.get(rr, rr)}")
                ultima = f"auto: parar ({r_seg})"
            if identidad.activa and not robot.siguiendo():
                identidad.olvidar()
                print("  ya no sigue: olvido la firma")
                fin_cooldown = time.monotonic() + float(acc["cooldown_s"])

            if not nueva:
                time.sleep(0.005)
                continue

            # ---- gestos ------------------------------------------------
            edad_proc = time.monotonic() - ahora
            fresca = hueco <= max_img and edad_proc <= max_img and est_ok
            bruto = pose.gesto if pose.visible else NINGUNO
            confirmado = conf.paso(bruto, fresca)
            contador = conf.contador
            if args.debug:
                print(f"[dbg] {ahora - t0:7.3f} gesto={bruto:<9} "
                      f"cont={contador:>2}/{conf.necesarios(bruto):<2} "
                      f"fresca={'si' if fresca else 'NO'} hueco={hueco:.3f}s "
                      f"estado={postura} edad_estado={edad_est:.3f}s modo={robot.modo} "
                      f"armado={'si' if conf.armado else 'no'} "
                      f"saludo={med_sal.cambios}/{med_sal.amplitud:.2f}{med_sal.lado or '-'}"
                      f"{'(bloq)' if med_sal.bloqueado else ''} "
                      f"yolo={'si' if persona.visible else 'no'} "
                      f"personas={len(candidatos)} "
                      f"id={'si' if identidad.activa else 'no'} "
                      f"id_d={_f(identidad.d_ultima, '.2f')} "
                      f"id_desc={identidad.descartadas} "
                      f"yolo_tam={_f(persona.tam, '.3f')} yolo_lat={_f(persona.lateral, '+.2f')} "
                      f"d={_f(seguidor.d, '.2f')}m marcha={'si' if seguidor.control.en_marcha else 'no'} "
                      f"atras={'si' if seguidor.control.atras else 'no'} "
                      f"vx={robot.vx:+.2f} wz={robot.wz:+.2f} cuerpo={robot.incl.estado} "
                      f"pitch_cmd={robot.incl.cmd:+.2f} imu={_f(_imu_grados(estado), '+.1f')} "
                      f"ms_yolo={persona.ms:.0f} ms_mp={ms_mp:.0f} "
                      f"repetidas={sin_nueva['repetida']} fallos={sin_nueva['fallo']}")
                sin_nueva.clear()
                if ahora - t_cal >= 2.0 and len(cal) >= 10:
                    print(f"[cal] mediana yolo_tam={statistics.median(cal):.3f} "
                          f"(n={len(cal)}, ultimos {len(cal) / 15:.0f} s)")
                    t_cal = ahora

            accion = resultado = ""
            e = estado.estado()

            def consumir(g: str) -> None:
                conf.consumido(g)
                if g == SALUDO and conf.rearmar:
                    saludo.bloquear()     # no vuelve a saltar hasta bajar el brazo

            if confirmado:
                accion, motivo = decidir(confirmado, postura, robot.modo)
                en_cooldown = time.monotonic() < fin_cooldown
                if accion == "nada":
                    consumir(confirmado)
                    resultado = motivo
                elif en_cooldown and accion != "parar":
                    # Pendiente: se ejecuta al acabar el cooldown si se sigue viendo.
                    if pendiente != confirmado:
                        print(f"\n  gesto {confirmado}: pendiente, se hara al acabar el cooldown")
                        ignoradas["pendiente"] += 1
                    pendiente = confirmado
                    accion = ""
                elif time.monotonic() - ahora > max_img:
                    consumir(confirmado)
                    resultado = "imagen_vieja"
                else:
                    consumir(confirmado)
                    pendiente = None
                    print(f"\n[{time.monotonic() - t0:6.1f} s] gesto {confirmado}, "
                          f"robot {postura}/{robot.modo} -> {accion}")
                    robot.llamadas = []
                    resultado = ejecutar(accion, robot)
                    print(f"  resultado: {MENSAJES.get(resultado, resultado)}")
                    lanzadas[accion] += 1
                    if accion == "seguir" and robot.siguiendo():
                        seguidor.empezar(time.monotonic())
                        if identidad.activa_cfg:
                            yo = de_la_pose(candidatos, pose.caja)
                            if identidad.fijar(imagen_vista, yo, time.monotonic()):
                                print("  firma guardada: solo sigo a esta persona")
                            else:
                                print("  [!] no he podido guardar la firma: "
                                      "sigo a la persona mas grande")
                    fin_cooldown = time.monotonic() + float(acc["cooldown_s"])
                if accion == "nada" or resultado == "imagen_vieja":
                    ignoradas[resultado] += 1
                    print(f"\n  gesto {confirmado}: no hago nada "
                          f"({MENSAJES.get(resultado, resultado)}, robot {postura}/{robot.modo})")
                if accion:
                    ultima = f"{confirmado}: {accion} ({resultado})"
            elif pendiente and bruto != pendiente:
                pendiente = None             # dejó de verse: ya no está pendiente

            n += 1
            n_persona += int(persona.visible)
            mode, altura, _ = estado.leer()
            mostrado = robot.modo if robot.siguiendo() else postura
            if args.log:
                filas.append([f"{ahora - t0:.3f}", int(persona.visible), int(pose.visible),
                              bruto, contador, confirmado or "",
                              med_sal.cambios, f"{med_sal.amplitud:.3f}", med_sal.lado,
                              _f((med_sal.x or {}).get("i"), ".3f", ""),
                              _f((med_sal.x or {}).get("d"), ".3f", ""),
                              e, postura, robot.modo,
                              "" if altura is None else f"{altura:.3f}", f"{hueco:.3f}",
                              _f(persona.lateral, ".4f", ""), _f(persona.tam, ".4f", ""),
                              _f(seguidor.tam, ".4f", ""), _f(seguidor.d, ".2f", ""),
                              f"{robot.vx:.3f}", f"{robot.wz:.3f}",
                              int(seguidor.control.en_marcha), int(seguidor.control.atras),
                              robot.incl.estado, f"{robot.incl.cmd:.3f}",
                              _f(_imu_grados(estado), ".1f", ""),
                              int(not sano), accion, resultado,
                              f"{persona.ms:.0f}", f"{ms_mp:.0f}"])

            if args.show:
                cd = max(0.0, fin_cooldown - time.monotonic())
                lineas = [
                    f"gesto: {bruto}  {contador}/{conf.necesarios(bruto)}"
                    + ("" if conf.armado else "  (deja de hacerlo para repetir)"),
                    f"estado: {mostrado}"
                    + (f"  (para en {max(0.0, float(cs['pausa_max_s']) - (ahora - seguidor.t_persona)):.1f} s)"
                       if robot.modo == PAUSADO else "")
                    + ("" if altura is None else f"  h {altura:.3f}"),
                    f"cuerpo: {robot.incl.estado} pitch {robot.incl.cmd:+.2f} rad"
                    f" (IMU {_f(_imu_grados(estado), '+.1f', '--')} grados, no se usa)"
                    + (f"  {robot.incl.aviso}" if robot.incl.aviso else ""),
                    f"vx {robot.vx:+.2f} m/s  wz {robot.wz:+.2f} rad/s"
                    + (f"  ({seguidor.motivo})" if robot.siguiendo() and seguidor.motivo else ""),
                    f"distancia: {_f(seguidor.d, '.2f', '--')} m  (yolo_tam "
                    f"{_f(persona.tam, '.2f', '--')} / obj {cs['tam_objetivo']:.2f})",
                    f"accion: {ultima}",
                    (f"sigo solo a 1 persona (de {len(candidatos)}): parecido "
                     f"{_f(identidad.d_ultima, '.2f', '--')} (0 = igual, "
                     f"umbral {float(identidad.cfg.get('umbral', 0.5)):.2f})"
                     if identidad.activa else f"personas: {len(candidatos)}"),
                    f"saludo: cambios {med_sal.cambios}/{cs_sal['cambios_min']}  amplitud "
                    f"{med_sal.amplitud:.2f}/{float(cs_sal['amplitud_min']):.2f}  "
                    f"lado {med_sal.lado or '-'}"
                    + ("  (baja el brazo para repetir)" if med_sal.bloqueado else "")
                    + ("" if saludo.activo else "  (DESACTIVADO)"),
                ]
                if not sano:
                    lineas.append("WATCHDOG: imagen o estado viejos -> velocidad 0")
                if args.dry_run:
                    lineas.append("DRY RUN")
                vis = det.dibujar(imagen_vista, pose, lineas)
                if persona.visible and persona.caja:
                    x1, y1, x2, y2 = persona.caja
                    cv2.rectangle(vis, (x1, y1), (x2, y2), (255, 255, 255), 2)
                if cd > 0:
                    h_img, w_img = vis.shape[:2]
                    txt = f"COOLDOWN {cd:.1f} s"
                    cv2.putText(vis, txt, (w_img // 2 - 150, h_img // 2), cv2.FONT_HERSHEY_SIMPLEX,
                                1.2, (0, 0, 0), 6)
                    cv2.putText(vis, txt, (w_img // 2 - 150, h_img // 2), cv2.FONT_HERSHEY_SIMPLEX,
                                1.2, (0, 0, 255), 2)
                    if pendiente:
                        cv2.putText(vis, f"pendiente: {pendiente}", (w_img // 2 - 120, h_img // 2 + 40),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                cv2.imshow("uc13 - gestos y seguimiento", vis)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            elif n % 15 == 0 and not args.debug:
                print(f"\r  {'persona' if persona.visible else '  ---  '}  "
                      f"gesto {bruto:<9} {contador:>2}/{conf.necesarios(bruto):<2}  "
                      f"{mostrado:<9} {robot.incl.estado:<11} vx {robot.vx:+.2f} "
                      f"wz {robot.wz:+.2f} d {_f(seguidor.d, '.1f', '--')} m  "
                      f"{det.fps:4.1f} fps  ", end="", flush=True)

    except Exception as ex:
        print(f"\n[ERROR] {type(ex).__name__}: {ex}")
        codigo = 1
    finally:
        t_fin = time.monotonic()
        signal.signal(signal.SIGINT, lambda *_: print("\n  ya estoy parando..."))
        print("\n\nparando...")
        if robot is not None:
            cerrar(robot)
        det.cerrar()
        if args.show:
            import cv2
            cv2.destroyAllWindows()
        signal.signal(signal.SIGINT, signal.default_int_handler)

    dur = t_fin - t0
    print(f"duracion {dur:.1f} s | {n} fotogramas | {det.fps:.1f} fps")
    if n:
        print(f"persona (YOLO) visible el {100 * n_persona / n:.0f} % del tiempo")
    print(f"acciones lanzadas: {dict(lanzadas) or '-'} | ignoradas: {dict(ignoradas) or '-'}")

    if args.log and filas:
        sha = git_sha()
        destino = (paths.EXPERIMENTS / "uc13_gesture_follow"
                   / f"{datetime.now():%Y-%m-%dT%H%M%S}_{sha}_gesture_follow")
        destino.mkdir(parents=True, exist_ok=True)
        with open(destino / "metrics.csv", "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["t", "persona", "pose", "gesto_bruto", "contador", "gesto_confirmado",
                        "saludo_cambios", "saludo_amplitud", "saludo_lado", "saludo_x_i",
                        "saludo_x_d",
                        "estado_robot", "postura_activa", "modo", "body_height", "edad_imagen",
                        "lateral", "tam", "tam_filtrado", "distancia_m", "vx", "wz",
                        "en_marcha", "marcha_atras", "cuerpo", "pitch_cmd", "pitch_imu",
                        "watchdog", "accion", "resultado", "ms_yolo", "ms_mediapipe"])
            w.writerows(filas)
        (destino / "manifest.json").write_text(json.dumps({
            "usecase": "uc13_gesture_follow",
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "git_sha": sha,
            "git": {"sha": git_sha(short=False), "dirty": git_dirty()},
            "modo": args.mode,
            "fuente": args.source,
            "camara": args.camera,
            "dry_run": args.dry_run,
            "config": cfg,
            "duracion_s": round(dur, 1),
            "fotogramas": n,
            "fps_medio": round(det.fps, 1),
            "persona_pct": round(100 * n_persona / max(n, 1), 1),
            "ciclos_watchdog": n_watchdog,
            "acciones_lanzadas": dict(lanzadas),
            "acciones_ignoradas": dict(ignoradas),
        }, indent=2, ensure_ascii=False))
        print(f"\nescrito: {destino}")

    return codigo


def _f(v, fmt: str, nada: str = "--") -> str:
    return nada if v is None else format(v, fmt)


def _candidatos(personas, imagen) -> list[Persona]:
    """Todas las personas de YOLO (o la que dé `detectar` si no sabe más)."""
    if hasattr(personas, "detectar_todas"):
        return personas.detectar_todas(imagen)
    p = personas.detectar(imagen)
    if p.visible and p.caja is None:
        p.caja = (0, 0, 1, 1)
    return [p] if p.visible else []


def _imu_grados(estado) -> float | None:
    """Cabeceo de rt/sportmodestate en grados. Solo para mostrar y registrar."""
    p = estado.leer_pitch()
    return None if p is None else math.degrees(p)


def cerrar(robot: Robot) -> None:
    """Cierre: Euler 0 (esperar IMU); si seguía y está recto, Move(0,0,0) +
    StopMove; después, el cierre seguro de uc12."""
    if not robot.incl.recto_y_estable():
        robot.enderezar()
    if robot.siguiendo():
        print("  seguia: Move(0,0,0) + StopMove")
        if robot.incl.recto_y_estable():
            try:
                robot.mover(0.0, 0.0)
            except Exception as e:
                print(f"  [!] Move(0,0,0) fallo: {e}. USA EL MANDO: L2+B")
        robot.modo = QUIETO
        try:
            robot.salida.llamar("StopMove")
            robot.llamadas.append("StopMove")
        except Exception as e:
            print(f"  [!] StopMove fallo: {e}. USA EL MANDO: L2+B")
        dormir(float(robot.c["espera_stopmove_s"]), interrumpible=False)
    if robot.postura() != TUMBADO:
        try:
            print(f"  cierre seguro (estado {robot.postura()})...")
            r = robot.tumbar(forzar=True)
            print(f"  {MENSAJES.get(r, r)}")
        except Abortar as e:
            print(f"  [!] {e}. Comprueba el robot. Si hace falta: L2+B")
        except Exception as e:
            print(f"fallo al tumbar: {e}. USA EL MANDO: L2+B")


if __name__ == "__main__":
    raise SystemExit(main())
