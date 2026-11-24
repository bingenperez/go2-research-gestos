#!/usr/bin/env python3
"""usecases/uc09_gesture_control/deploy/gesture_control.py

uc09 — El robot responde a gestos de una persona.

    cámara ──► MediaPipe Pose ──► gesto ──► confirmación ──► acción Sport Mode
                                             (~1 s + cooldown)   (SportClient)

GESTOS Y ACCIONES
-----------------
    mano levantada    de pie   -> Hello()
                      tumbado  -> StandUp() + BalanceStand(), espera a que
                                  esté estable, y Hello()
    brazos cruzados   de pie   -> StopMove() + StandDown()   (se queda tumbado)
                      tumbado  -> nada

Solo acciones discretas de Sport Mode. NUNCA Move, NUNCA acrobacias, nada de
políticas RL. Por eso no hay modo simulación: unitree_mujoco no emula Sport
Mode.

ESTADO DEL ROBOT
----------------
Se lee de rt/sportmodestate (mode y body_height). Antes de cada llamada a
SportClient se comprueba que el estado es compatible: Hello solo de pie,
StandUp solo tumbado. Si no llega estado reciente, el estado es DESCONOCIDO y
no se hace nada. Los valores de mode y el umbral de altura están SIN CALIBRAR
con el robot: ejecutar primero con --estado (ver configs/gestures.yaml).

SEGURIDAD
---------
    - confirmación escribiendo "si" antes de mover el robot real
    - un gesto cuenta solo si se mantiene N fotogramas seguidos; tras cada
      acción hay cooldown y hay que bajar los brazos para re-armar
    - watchdog: si no llega imagen nueva en 0.5 s, no se lanza ninguna acción
    - al terminar (fin, error, q o Ctrl-C): StopMove + StandDown, tumbado
    - --dry-run no crea SportClient: solo imprime lo que haría
    - la parada de emergencia real sigue siendo el mando: L2 + B

Uso (dentro de la caja; ver README):
    # Sin robot, con la webcam del portátil: prueba la máquina de estados
    python3 usecases/uc09_gesture_control/deploy/gesture_control.py \\
        --source webcam --dry-run --show

    # Calibrar el estado del robot (solo lectura)
    python3 usecases/uc09_gesture_control/deploy/gesture_control.py \\
        --iface <IFACE> --estado

    # Robot real, primero sin mover nada
    python3 usecases/uc09_gesture_control/deploy/gesture_control.py \\
        --mode real --iface <IFACE> --source robot --show --dry-run
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse   # noqa: E402
import csv        # noqa: E402
import json       # noqa: E402
import signal     # noqa: E402
import sys        # noqa: E402
import threading  # noqa: E402
import time       # noqa: E402
from collections import Counter  # noqa: E402
from datetime import datetime    # noqa: E402
from pathlib import Path         # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from go2core import paths                              # noqa: E402
from go2core.control import contract as ct              # noqa: E402
from go2core.logging.run import git_dirty, git_sha      # noqa: E402
from perception.pose import CRUZADOS, MANO, NINGUNO     # noqa: E402

UC = Path(__file__).resolve().parents[1]

DE_PIE, TUMBADO, DESCONOCIDO = "DE_PIE", "TUMBADO", "DESCONOCIDO"

PARAR = {"flag": False}


def dormir(s: float, interrumpible: bool = True) -> bool:
    """Espera s segundos. Devuelve False si Ctrl-C la ha cortado."""
    fin = time.monotonic() + s
    while time.monotonic() < fin:
        if interrumpible and PARAR["flag"]:
            return False
        time.sleep(0.05)
    return True


# ===========================================================================
# Estado del robot
# ===========================================================================
def clasificar_estado(mode: int, altura: float, cfg: dict) -> str:
    """mode + body_height -> DE_PIE / TUMBADO / DESCONOCIDO.

    Las dos señales tienen que estar de acuerdo. Si se contradicen (un modo de
    pie con el cuerpo en el suelo, o al revés), DESCONOCIDO: mejor no hacer
    nada que actuar sobre un estado que no se entiende.
    """
    de_pie_m = mode in cfg["modos_de_pie"]
    tumbado_m = mode in cfg["modos_tumbado"]
    alto = altura > float(cfg["altura_umbral"])
    if de_pie_m and alto:
        return DE_PIE
    if tumbado_m and not alto:
        return TUMBADO
    if not de_pie_m and not tumbado_m and not alto:
        return TUMBADO            # modo no listado, pero el cuerpo está en el suelo
    return DESCONOCIDO


class EstadoSport:
    """Suscripción a rt/sportmodestate. Solo lectura."""

    def __init__(self, cfg: dict, max_edad_s: float) -> None:
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import SportModeState_
        self.cfg = cfg
        self.max_edad_s = max_edad_s
        self._lock = threading.Lock()
        self.mode: int | None = None
        self.altura: float | None = None
        self.progreso: float | None = None
        self._t = 0.0
        self.n = 0
        self.sub = ChannelSubscriber("rt/sportmodestate", SportModeState_)
        self.sub.Init(self._on, 10)

    def _on(self, m) -> None:
        with self._lock:
            self.mode = int(m.mode)
            self.altura = float(m.body_height)
            self.progreso = float(getattr(m, "progress", 0.0))
            self._t = time.monotonic()
            self.n += 1

    def leer(self) -> tuple[int | None, float | None, float]:
        with self._lock:
            edad = time.monotonic() - self._t if self._t else float("inf")
            return self.mode, self.altura, edad

    def estado(self) -> str:
        mode, altura, edad = self.leer()
        if mode is None or altura is None or edad > self.max_edad_s:
            return DESCONOCIDO
        return clasificar_estado(mode, altura, self.cfg)

    def esperar_primero(self, timeout: float) -> bool:
        fin = time.monotonic() + timeout
        while time.monotonic() < fin:
            if self.n > 0:
                return True
            time.sleep(0.05)
        return False


class EstadoSimulado:
    """Estado inventado, solo para --dry-run con la webcam y sin robot.

    Lo actualizan las acciones "de mentira", así se puede recorrer la máquina
    de estados entera en el portátil.
    """

    def __init__(self, inicial: str) -> None:
        self.valor = inicial
        self.n = 1

    def leer(self) -> tuple[None, None, float]:
        return None, None, 0.0

    def estado(self) -> str:
        return self.valor

    def esperar_primero(self, timeout: float) -> bool:
        return True


# ===========================================================================
# Salidas: robot real o dry-run
# ===========================================================================
class SalidaRobot:
    """SportClient DIRECTO, como uc04 (Go2Controller no movió el robot).

    Solo se usan StandUp, BalanceStand, StopMove, StandDown y Hello. Nunca Move.
    """

    nombre = "robot real (SportClient)"
    PERMITIDAS = {"StandUp", "BalanceStand", "StopMove", "StandDown", "Hello"}

    def __init__(self) -> None:
        from unitree_sdk2py.go2.sport.sport_client import SportClient
        self.sport = SportClient()
        self.sport.SetTimeout(10.0)
        self.sport.Init()

    def llamar(self, nombre: str) -> int:
        if nombre not in self.PERMITIDAS:
            raise ValueError(f"accion no permitida en uc09: {nombre}")
        print(f"  -> SportClient.{nombre}()")
        codigo = getattr(self.sport, nombre)()
        if codigo not in (0, None):
            print(f"  [!] {nombre} devolvio el codigo {codigo}")
        return int(codigo or 0)


class SalidaNula:
    """--dry-run: no crea SportClient ni envía nada."""

    nombre = "DRY RUN (no se envia nada)"

    def llamar(self, nombre: str) -> int:
        print(f"  [DRY RUN] haria: SportClient.{nombre}()")
        return 0


class Robot:
    """Acciones compuestas, con el estado comprobado antes de cada llamada."""

    def __init__(self, salida, estado, cfg_acc: dict, dry_run: bool) -> None:
        self.salida = salida
        self.estado = estado
        self.c = cfg_acc
        self.dry_run = dry_run
        self.simulado = isinstance(estado, EstadoSimulado)
        # En dry-run con el estado REAL (camara del robot), las acciones de
        # mentira no cambian el estado: las comprobaciones posteriores a la
        # primera llamada no tienen sentido y se saltan.
        self.virtual = dry_run and not self.simulado

    def _es(self, esperado: str) -> bool:
        return self.estado.estado() == esperado

    def _simular(self, valor: str) -> None:
        if self.simulado:
            self.estado.valor = valor

    def esperar(self, esperado: str, durante: float, timeout: float,
                interrumpible: bool = True) -> bool:
        """True si el estado es `esperado` sin interrupción durante `durante` s."""
        if self.dry_run:
            return True
        fin = time.monotonic() + timeout
        t_ok = None
        while time.monotonic() < fin:
            if interrumpible and PARAR["flag"]:
                return False
            if self._es(esperado):
                t_ok = t_ok or time.monotonic()
                if time.monotonic() - t_ok >= durante:
                    return True
            else:
                t_ok = None
            time.sleep(0.05)
        return False

    # ------------------------------------------------------------------
    def levantar(self) -> str:
        if not self._es(TUMBADO):
            return "incompatible"
        if self.salida.llamar("StandUp") != 0:
            return "error_standup"
        if not dormir(self.c["espera_standup_s"]):
            return "interrumpido"
        # Antes de BalanceStand, comprobar que StandUp ha surtido efecto.
        if not self.dry_run and self._es(TUMBADO):
            return "no_se_levanto"
        if self.salida.llamar("BalanceStand") != 0:
            return "error_balancestand"
        if not dormir(self.c["espera_balance_s"]):
            return "interrumpido"
        self._simular(DE_PIE)
        if not self.esperar(DE_PIE, self.c["estable_s"], self.c["timeout_estable_s"]):
            return "inestable"
        return "ok"

    def hello(self) -> str:
        if not self.virtual and not self._es(DE_PIE):
            return "incompatible"
        if self.salida.llamar("Hello") != 0:
            return "error_hello"
        if not dormir(self.c["espera_hello_s"]):
            return "interrumpido"
        return "ok"

    def tumbar(self, interrumpible: bool = True) -> str:
        """StopMove + StandDown. Se usa tambien al salir, sin interrupcion."""
        self.salida.llamar("StopMove")
        dormir(0.5, interrumpible=False)
        if self.salida.llamar("StandDown") != 0:
            return "error_standdown"
        self._simular(TUMBADO)
        if not self.esperar(TUMBADO, 0.0, self.c["espera_standdown_s"], interrumpible):
            return "sin_confirmar_tumbado"
        return "ok"


# ===========================================================================
# Maquina de estados gesto -> accion
# ===========================================================================
class Confirmador:
    """Un gesto cuenta si se mantiene N fotogramas frescos seguidos.

    Tras una accion (consumido), con `rearmar` hay que pasar por "ninguno"
    antes de que vuelva a contar: mantener la mano levantada no dispara un
    saludo detras de otro.
    """

    def __init__(self, frames: dict, rearmar: bool) -> None:
        self.frames = {MANO: int(frames["mano"]), CRUZADOS: int(frames["cruzados"])}
        self.rearmar = rearmar
        self.previo = NINGUNO
        self.contador = 0
        self.armado = True

    def reiniciar(self) -> None:
        self.previo, self.contador = NINGUNO, 0

    def paso(self, gesto: str, fresca: bool) -> str | None:
        if not fresca:
            self.reiniciar()      # watchdog: una imagen vieja rompe la racha
            return None
        if gesto != self.previo:
            self.previo, self.contador = gesto, 0
        if gesto == NINGUNO:
            self.armado = True
            return None
        self.contador += 1
        if not self.armado:
            return None
        return gesto if self.contador >= self.frames[gesto] else None

    def consumido(self) -> None:
        self.contador = 0
        if self.rearmar:
            self.armado = False

    def necesarios(self, gesto: str) -> int:
        return self.frames.get(gesto, 0)


def decidir(gesto: str, estado: str) -> tuple[str, str]:
    """(accion, motivo). accion "nada" significa que no se hace nada."""
    if estado == DESCONOCIDO:
        return "nada", "estado_desconocido"
    if gesto == MANO:
        return ("hello", "") if estado == DE_PIE else ("levantar_y_hello", "")
    if gesto == CRUZADOS:
        return ("tumbar", "") if estado == DE_PIE else ("nada", "ya_tumbado")
    return "nada", "sin_gesto"


def ejecutar(accion: str, robot: Robot) -> str:
    if accion == "hello":
        return robot.hello()
    if accion == "tumbar":
        return robot.tumbar()
    if accion == "levantar_y_hello":
        r = robot.levantar()
        if r != "ok":
            # No se saluda sin estar estable de pie. Si se quedo a medias, al
            # suelo. Si fue Ctrl-C, el finally de main() lo tumba.
            if r not in ("interrumpido", "incompatible"):
                robot.tumbar()
            return f"levantar:{r}"
        return robot.hello()
    raise ValueError(accion)


# ===========================================================================
def modo_estado(estado: EstadoSport, segundos: float = 10.0) -> int:
    """--estado: imprime lo que llega de rt/sportmodestate. No mueve nada."""
    print("leyendo rt/sportmodestate (solo lectura). Pon el robot de pie y")
    print("tumbado con el mando y anota mode y body_height en cada caso.\n")
    if not estado.esperar_primero(3.0):
        print("No llega rt/sportmodestate. Revisa la red (GUIA 4.2) y la app del movil.")
        return 1
    t0 = time.monotonic()
    while not PARAR["flag"] and time.monotonic() - t0 < segundos:
        mode, h, edad = estado.leer()
        print(f"\r  mode {mode:>3}  body_height {h:6.3f} m  progress "
              f"{estado.progreso:5.2f}  edad {edad * 1000:5.0f} ms  ->  "
              f"{estado.estado():<11}", end="", flush=True)
        time.sleep(0.2)
    print(f"\n\n{estado.n} mensajes. Si la clasificacion no cuadra, ajusta "
          f"`estado_robot` en configs/gestures.yaml.")
    return 0


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
    p.add_argument("--config", default=str(UC / "configs" / "gestures.yaml"))
    p.add_argument("--show", action="store_true",
                   help="ventana con esqueleto, gesto, estado y accion")
    p.add_argument("--duration", type=float, default=300.0)
    p.add_argument("--dry-run", action="store_true",
                   help="detecta y decide, pero NO crea SportClient ni envia nada")
    p.add_argument("--log", action="store_true", help="registrar el episodio")
    p.add_argument("--estado", action="store_true",
                   help="solo lectura: muestra rt/sportmodestate 10 s y sale")
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    c = ct.load_contract(paths.CONTRACT)
    dds = c["dds"][args.mode]
    iface = args.iface or paths.iface() or dds["interface"]
    domain = paths.domain() if paths.domain() is not None else dds["domain_id"]
    seg, acc = cfg["seguridad"], cfg["acciones"]

    signal.signal(signal.SIGINT, lambda *_: PARAR.__setitem__("flag", True))

    mueve = not args.dry_run and not args.estado
    necesita_dds = args.estado or mueve or args.source == "robot"

    print("uc09 — control por gestos")
    print(f"  modo    : {args.mode} (domain {domain}, {iface})")
    print(f"  fuente  : {args.source}")
    print(f"  ventana : {'si' if args.show else 'no'}")
    if args.dry_run:
        print("  DRY RUN: no se crea SportClient ni se envia nada")
    print()

    if necesita_dds:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        ChannelFactoryInitialize(domain, iface)
        estado = EstadoSport(cfg["estado_robot"], float(seg["estado_max_edad_s"]))
    else:
        estado = EstadoSimulado(cfg["estado_robot"].get("simulado_inicial", TUMBADO))
        print(f"  sin robot: estado SIMULADO, empieza {estado.valor}\n")

    if args.estado:
        return modo_estado(estado)

    if mueve:
        print("  ROBOT FISICO. Confirma:")
        print("   - docs/SAFETY.md leido")
        print("   - espacio libre de 3 m alrededor")
        print("   - mando en la mano (L2+B para el robot)")
        print("   - estado_robot calibrado con --estado")
        if input("\n  Escribe 'si' para continuar: ").strip().lower() != "si":
            return 1

    print("cargando MediaPipe Pose...")
    from perception.pose import DetectorGestos
    det = DetectorGestos(cfg, camara=args.camera, fuente=args.source,
                         iface=iface, domain=domain, init_dds=False)
    print("detector listo")

    if necesita_dds and not estado.esperar_primero(2.0):
        if mueve:
            print("No llega rt/sportmodestate: sin estado no se mueve el robot.")
            det.cerrar()
            return 1
        print("[!] No llega rt/sportmodestate: estado DESCONOCIDO, no se haria nada.")

    salida = None
    robot = None
    conf = Confirmador(cfg["gestos"]["frames_necesarios"], bool(acc.get("rearmar", True)))
    filas: list = []
    lanzadas: Counter = Counter()
    ignoradas: Counter = Counter()
    n = n_persona = 0
    t0 = time.monotonic()
    codigo = 0

    if args.show:
        import cv2

    try:
        salida = SalidaRobot() if mueve else SalidaNula()
        robot = Robot(salida, estado, acc, args.dry_run)
        print(f"salida: {salida.nombre}")

        # Arranque: el robot tiene que acabar de pie y estable.
        e = estado.estado()
        print(f"estado inicial: {e}")
        if e == TUMBADO:
            print("  levantando el robot (StandUp + BalanceStand)...")
            r = robot.levantar()
            if r != "ok":
                print(f"  no se pudo levantar ({r}). Fin.")
                return 1
        elif e == DE_PIE:
            salida.llamar("BalanceStand")
            dormir(acc["espera_balance_s"])
        elif mueve:
            print("  estado DESCONOCIDO: no se mueve el robot. Calibra con --estado.")
            return 1

        print("\nen marcha. Mano levantada = saludo, brazos cruzados = tumbarse.")
        print("q o Ctrl-C para parar.\n")

        t0 = time.monotonic()
        t_ultima_img = t0
        fin_cooldown = 0.0
        ultima = "-"
        while not PARAR["flag"] and time.monotonic() - t0 < args.duration:
            imagen = det.leer_imagen()
            ahora = time.monotonic()
            if imagen is None:
                conf.reiniciar()
                if ahora - t_ultima_img > float(seg["sin_imagen_salir_s"]):
                    print("\nla camara ha dejado de dar imagen")
                    break
                time.sleep(0.005)
                continue

            hueco = ahora - t_ultima_img
            t_ultima_img = ahora
            pose = det.detectar(imagen)
            edad = time.monotonic() - ahora
            # Watchdog: una imagen que llega tras un hueco largo, o que tarda
            # demasiado en procesarse, no cuenta para confirmar un gesto.
            fresca = (hueco <= float(seg["imagen_max_edad_s"])
                      and edad <= float(seg["imagen_max_edad_s"]))
            bruto = pose.gesto if pose.visible else NINGUNO
            confirmado = conf.paso(bruto, fresca)
            contador = conf.contador

            accion = resultado = ""
            if confirmado:
                conf.consumido()
                e = estado.estado()
                if time.monotonic() < fin_cooldown:
                    accion, resultado = "nada", "cooldown"
                else:
                    accion, motivo = decidir(confirmado, e)
                    if accion == "nada":
                        resultado = motivo
                    elif time.monotonic() - ahora > float(seg["imagen_max_edad_s"]):
                        resultado = "imagen_vieja"
                    else:
                        print(f"\n[{time.monotonic() - t0:6.1f} s] gesto {confirmado}, "
                              f"robot {e} -> {accion}")
                        resultado = ejecutar(accion, robot)
                        print(f"  resultado: {resultado}")
                        lanzadas[accion] += 1
                        fin_cooldown = time.monotonic() + float(acc["cooldown_s"])
                if accion == "nada" or resultado == "imagen_vieja":
                    ignoradas[resultado] += 1
                    print(f"\n  gesto {confirmado} ignorado: {resultado}")
                ultima = f"{confirmado}: {accion} ({resultado})"

            n += 1
            n_persona += int(pose.visible)
            mode, altura, _ = estado.leer()
            e = estado.estado()
            if args.log:
                filas.append([f"{ahora - t0:.3f}", int(pose.visible), bruto, contador,
                              confirmado or "", e,
                              "" if mode is None else mode,
                              "" if altura is None else f"{altura:.3f}",
                              f"{hueco:.3f}", accion, resultado])

            if args.show:
                cd = max(0.0, fin_cooldown - time.monotonic())
                lineas = [
                    f"gesto: {bruto}  {contador}/{conf.necesarios(bruto)}"
                    + ("" if conf.armado else "  (baja los brazos)"),
                    f"robot: {e}"
                    + ("" if mode is None else f"  mode {mode}  h {altura:.2f}"),
                    f"accion: {ultima}",
                    f"cooldown: {cd:.1f} s" if cd > 0 else "listo",
                ]
                if not fresca:
                    lineas.append("IMAGEN VIEJA: sin acciones")
                if args.dry_run:
                    lineas.append("DRY RUN")
                cv2.imshow("uc09 - gestos", det.dibujar(imagen, pose, lineas))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            elif n % 15 == 0:
                print(f"\r  {'persona' if pose.visible else '  ---  '}  "
                      f"gesto {bruto:<9} {contador:>2}/{conf.necesarios(bruto):<2}  "
                      f"robot {e:<11}  {det.fps:4.1f} fps  ", end="", flush=True)

    except Exception as ex:
        print(f"\n[ERROR] {type(ex).__name__}: {ex}")
        codigo = 1
    finally:
        print("\n\nparando...")
        # La guia pide terminar con el robot tumbado. Solo se omite si el
        # estado confirma que ya lo esta.
        # En dry-run solo imprime lo que haria.
        if robot is not None and estado.estado() != TUMBADO:
            try:
                print("  tumbando el robot (StopMove + StandDown)...")
                r = robot.tumbar(interrumpible=False)
                if r != "ok":
                    print(f"  [!] {r}. Comprueba el robot. Si hace falta: L2+B")
            except Exception as ex:
                print(f"fallo al tumbar: {ex}. USA EL MANDO: L2+B")
        det.cerrar()
        if args.show:
            import cv2
            cv2.destroyAllWindows()

    dur = time.monotonic() - t0
    print(f"duracion {dur:.1f} s | {n} fotogramas | {det.fps:.1f} fps")
    if n:
        print(f"persona visible el {100 * n_persona / n:.0f} % del tiempo")
    print(f"acciones lanzadas: {dict(lanzadas) or '-'} | ignoradas: {dict(ignoradas) or '-'}")

    if args.log and filas:
        sha = git_sha()
        destino = (paths.EXPERIMENTS / "uc09_gesture_control"
                   / f"{datetime.now():%Y-%m-%dT%H%M%S}_{sha}_gestures")
        destino.mkdir(parents=True, exist_ok=True)
        with open(destino / "metrics.csv", "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["t", "persona", "gesto_bruto", "contador", "gesto_confirmado",
                        "estado_robot", "mode", "body_height", "edad_imagen",
                        "accion", "resultado"])
            w.writerows(filas)
        (destino / "manifest.json").write_text(json.dumps({
            "usecase": "uc09_gesture_control",
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
            "acciones_lanzadas": dict(lanzadas),
            "acciones_ignoradas": dict(ignoradas),
        }, indent=2, ensure_ascii=False))
        print(f"\nescrito: {destino}")

    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
