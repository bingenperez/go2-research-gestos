#!/usr/bin/env python3
"""usecases/uc10_text_commands/deploy/text_commands.py

uc10 — Ordenes escritas en la terminal.

    teclado ──► orden ──► estado del robot ──► accion Sport Mode (SportClient)

ORDENES
-------
    levantate | arriba | l    de pie: StandUp + BalanceStand (sentado: RiseSit)
    tumbate   | abajo  | t    StopMove + StandDown (sentado: antes RiseSit)
    saluda    | hola   | h    Hello; si no esta de pie, primero se levanta
    sientate  | s             Sit, solo de pie
    estado                    de pie, sentado o tumbado
    ayuda                     esta lista
    salir     | q             termina: de pie y estable se queda DE PIE (solo
                              StopMove); si no, cierre seguro (tumbado)

Da igual mayusculas o minusculas, con tilde o sin ella.

Solo acciones discretas de Sport Mode. NUNCA Move, nada de politicas RL. Por
eso no hay modo simulacion: unitree_mujoco no emula Sport Mode.

ESTADO DEL ROBOT
----------------
Se lee de rt/sportmodestate con la clase de uc09 (EstadoSport), anadiendo
SENTADO. Antes de cada llamada a SportClient se comprueba que el estado es
compatible. Si no llega estado reciente, DESCONOCIDO: no se hace nada.
Nuestro Go2 da mode 0 en todas las posturas, asi que se clasifica por rangos
de body_height (configs/text_commands.yaml, medido con --estado). StandDown
solo se envia con DE_PIE confirmado; sentado o dudoso, antes RiseSit.

SEGURIDAD
---------
    - confirmacion escribiendo "si" antes de mover el robot real
    - una orden a la vez: lo tecleado mientras actua se descarta
    - solo StandUp, BalanceStand, StopMove, StandDown, Hello, Sit y RiseSit
    - al terminar (salir, q, Ctrl-D, Ctrl-C o error), ver cerrar():
          de pie y estable, sin orden a medias  -> StopMove, SE QUEDA DE PIE
          orden a medias (Ctrl-C) o error       -> cierre seguro (tumbado)
          sentado o dudoso                      -> cierre seguro (tumbado)
          tumbado                               -> nada
          sin datos                             -> nada, usar el mando
      y el mensaje final dice en que postura queda
    - --dry-run no crea SportClient: imprime lo que haria y simula el estado
    - la parada de emergencia real sigue siendo el mando: L2 + B

Uso (dentro de la caja, GUIA 4.4):
    # Sin robot
    python3 usecases/uc10_text_commands/deploy/text_commands.py --dry-run

    # Calibrar el estado (solo lectura)
    python3 usecases/uc10_text_commands/deploy/text_commands.py --iface <IFACE> --estado

    # Robot real
    python3 usecases/uc10_text_commands/deploy/text_commands.py --mode real --iface <IFACE> --log
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse     # noqa: E402
import csv          # noqa: E402
import json         # noqa: E402
import signal       # noqa: E402
import sys          # noqa: E402
import time         # noqa: E402
import unicodedata  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import datetime    # noqa: E402
from pathlib import Path         # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from go2core import paths                              # noqa: E402
from go2core.control import contract as ct              # noqa: E402
from go2core.logging.run import git_dirty, git_sha      # noqa: E402

# Lectura de rt/sportmodestate, estado simulado y clasificacion de pie/tumbado:
# de uc09, sin duplicarlo. Importarlo carga cv2 y numpy (ya en la imagen), no
# MediaPipe.
sys.path.insert(0, str(paths.USECASES / "uc09_gesture_control" / "deploy"))
import gesture_control as uc09  # noqa: E402

UC = Path(__file__).resolve().parents[1]

DE_PIE, TUMBADO, DESCONOCIDO = uc09.DE_PIE, uc09.TUMBADO, uc09.DESCONOCIDO
SENTADO = "SENTADO"

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
# Ordenes
# ===========================================================================
VOCABULARIO = {
    "levantar": ["levantate", "arriba", "l"],
    "tumbar": ["tumbate", "abajo", "t"],
    "saludar": ["saluda", "hola", "h"],
    "sentar": ["sientate", "s"],
    "estado": ["estado"],
    "ayuda": ["ayuda"],
    "salir": ["salir", "q"],
}
_TEXTO_A_ORDEN = {t: o for o, textos in VOCABULARIO.items() for t in textos}

AYUDA = """\
  levantate | arriba | l    ponerse de pie (sentado: RiseSit)
  tumbate   | abajo  | t    tumbarse (StopMove + StandDown)
  saluda    | hola   | h    saludar; si no esta de pie, primero se levanta
  sientate  | s             sentarse, solo de pie
  estado                    de pie, sentado o tumbado
  ayuda                     esta lista
  salir     | q             terminar (de pie se queda de pie; si no, cierre seguro)"""


def normalizar(texto: str) -> str:
    """Minusculas, sin tildes, sin signos y sin espacios sobrantes."""
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    for signo in "¡!¿?.,;:":
        t = t.replace(signo, " ")
    return " ".join(t.split())


def interpretar(texto: str) -> str | None:
    """Texto escrito -> nombre de la orden, o None si no se entiende."""
    return _TEXTO_A_ORDEN.get(normalizar(texto))


# ===========================================================================
# Estado del robot
# ===========================================================================
def clasificar_postura(mode: int, altura: float, cfg: dict) -> str:
    """mode + body_height -> DE_PIE / SENTADO / TUMBADO / DESCONOCIDO.

    Por defecto, por RANGOS DE ALTURA: el Go2 del grupo da mode 0 en todas las
    posturas (medido 2026-11-17). Una altura fuera de los tres rangos es
    DESCONOCIDO. Con usar_mode: true, la logica por mode de uc09 mas SENTADO.
    """
    if cfg.get("usar_mode", "rangos_altura" not in cfg):
        if mode in cfg.get("modos_sentado", []):
            return SENTADO
        return uc09.clasificar_estado(mode, altura, cfg)
    for clave, postura in (("tumbado", TUMBADO), ("sentado", SENTADO),
                           ("de_pie", DE_PIE)):
        lo, hi = cfg["rangos_altura"][clave]
        if float(lo) <= altura <= float(hi):
            return postura
    return DESCONOCIDO


class EstadoPostura(uc09.EstadoSport):
    """rt/sportmodestate (uc09) con tres posturas: de pie, sentado, tumbado."""

    def estado(self) -> str:
        mode, altura, edad = self.leer()
        if mode is None or altura is None or edad > self.max_edad_s:
            return DESCONOCIDO
        return clasificar_postura(mode, altura, self.cfg)

    def datos_frescos(self) -> bool:
        """Hay estado reciente. Separa "dudoso" (altura entre rangos) de
        "sin datos" (no llega rt/sportmodestate)."""
        _, altura, edad = self.leer()
        return altura is not None and edad <= self.max_edad_s


# ===========================================================================
# Salidas
# ===========================================================================
class SalidaRobot:
    """SportClient DIRECTO, como uc04 y uc09. Lista blanca de llamadas."""

    nombre = "robot real (SportClient)"
    PERMITIDAS = {"StandUp", "BalanceStand", "StopMove", "StandDown",
                  "Hello", "Sit", "RiseSit"}

    def __init__(self) -> None:
        from unitree_sdk2py.go2.sport.sport_client import SportClient
        self.sport = SportClient()
        self.sport.SetTimeout(10.0)
        self.sport.Init()

    def llamar(self, nombre: str) -> int:
        if nombre not in self.PERMITIDAS:
            raise ValueError(f"accion no permitida en uc10: {nombre}")
        print(f"  -> SportClient.{nombre}()")
        codigo = getattr(self.sport, nombre)()
        return int(codigo or 0)


class SalidaNula:
    """--dry-run: no crea SportClient ni envia nada."""

    nombre = "DRY RUN (no se envia nada)"

    def llamar(self, nombre: str) -> int:
        if nombre not in SalidaRobot.PERMITIDAS:
            raise ValueError(f"accion no permitida en uc10: {nombre}")
        print(f"  [DRY RUN] haria: SportClient.{nombre}()")
        return 0


class Abortar(Exception):
    """Corta una orden a medias. El mensaje es el resultado que se registra."""


class Robot:
    """Las ordenes, con el estado comprobado antes de cada llamada.

    En dry-run el estado es simulado: las esperas de estado lo fijan al valor
    esperado en lugar de esperar, asi la secuencia se recorre entera.
    """

    def __init__(self, salida, estado, cfg_acc: dict, cfg_estado: dict) -> None:
        self.salida = salida
        self.estado = estado
        self.c = cfg_acc
        # Altura por encima de la cual el cuerpo se ha separado del suelo.
        rangos = cfg_estado.get("rangos_altura")
        self.suelo = (float(rangos["tumbado"][1]) if rangos
                      else float(cfg_estado["altura_umbral"]))
        self.simulado = isinstance(estado, uc09.EstadoSimulado)
        self.llamadas: list[str] = []
        # Orden empezada y no terminada con normalidad (Ctrl-C a mitad, o
        # cortada por un fallo). Con esto activo, el cierre es el seguro.
        self.en_accion = False

    def postura(self) -> str:
        return self.estado.estado()

    def de_pie_estable(self) -> bool:
        """DE_PIE sin interrupcion durante estable_s (lo mismo que se exige al
        levantarse). Espera como mucho timeout_estable_cierre_s. No se corta
        con Ctrl-C (es parte del cierre)."""
        if self.postura() != DE_PIE:
            return False
        try:
            self._esperar_estado(DE_PIE, float(self.c["estable_s"]),
                                 float(self.c.get("timeout_estable_cierre_s", 3.0)),
                                 interrumpible=False)
        except Abortar:
            return False
        return True

    # -- piezas --------------------------------------------------------
    def _llamar(self, nombre: str, requiere: set[str] | None = None) -> None:
        if requiere is not None:
            p = self.postura()
            if p not in requiere:
                raise Abortar(f"incompatible:{nombre}_con_{p}")
        codigo = self.salida.llamar(nombre)
        self.llamadas.append(nombre)
        if codigo != 0:
            raise Abortar(f"error:{nombre}_codigo_{codigo}")

    def _dormir(self, clave: str, interrumpible: bool = True) -> None:
        if not dormir(float(self.c[clave]), interrumpible):
            raise Abortar("interrumpido")

    def _esperar_estado(self, esperado: str, durante: float, timeout: float,
                        interrumpible: bool = True) -> None:
        if self.simulado:
            self.estado.valor = esperado
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
        """Tras StandUp: el cuerpo tiene que haberse levantado del suelo."""
        if self.simulado:
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

    def _estable(self) -> None:
        self._esperar_estado(DE_PIE, float(self.c["estable_s"]),
                             float(self.c["timeout_estable_s"]))

    # -- ordenes -------------------------------------------------------
    def levantar(self) -> str:
        p = self.postura()
        if p == DE_PIE:
            return "ya_de_pie"
        if p == SENTADO:
            self._llamar("RiseSit", {SENTADO})
            self._estable()
            return "ok"
        if p == TUMBADO:
            self._llamar("StandUp", {TUMBADO})
            self._dormir("espera_standup_s")
            self._esperar_alto()
            self._llamar("BalanceStand")
            self._dormir("espera_balance_s")
            self._estable()
            return "ok"
        return "estado_desconocido"

    def _datos_frescos(self) -> bool:
        return self.simulado or self.estado.datos_frescos()

    def tumbar(self, forzar: bool = False) -> str:
        """StopMove + StandDown, SOLO con DE_PIE confirmado.

        StandDown sobre un robot sentado no esta claro que sea seguro (se vio
        el 2026-11-17: el cierre estuvo a punto de lanzarlo). Por eso:
          DE_PIE                       -> StopMove + StandDown
          SENTADO, o dudoso (con forzar,
          datos frescos entre rangos)  -> RiseSit, esperar DE_PIE, y entonces
                                          StopMove + StandDown. Si no llega a
                                          DE_PIE, NO se envia StandDown.
          sin datos (con forzar)       -> nada: no se manda nada a ciegas.
        forzar = cierre: no se interrumpe con Ctrl-C.
        """
        p = self.postura()
        if p == TUMBADO:
            return "ya_tumbado"
        if p == DESCONOCIDO:
            if not forzar:
                return "estado_desconocido"
            if not self._datos_frescos():
                return "sin_estado_usa_el_mando"
        if p in (SENTADO, DESCONOCIDO):
            print(f"  {p.lower()}: primero RiseSit y esperar a que este de pie")
            self._llamar("RiseSit", None if forzar else {SENTADO})
            try:
                self._esperar_estado(DE_PIE, float(self.c["estable_s"]),
                                     float(self.c["timeout_estable_s"]),
                                     interrumpible=not forzar)
            except Abortar as e:
                if str(e) == "interrumpido":
                    raise
                return "no_de_pie_tras_risesit_usa_el_mando"
        self._llamar("StopMove", {DE_PIE})
        self._dormir("espera_stopmove_s", interrumpible=not forzar)
        self._llamar("StandDown", {DE_PIE})
        self._esperar_estado(TUMBADO, 0.0, float(self.c["timeout_standdown_s"]),
                             interrumpible=not forzar)
        return "ok"

    def saludar(self) -> str:
        p = self.postura()
        if p == DESCONOCIDO:
            return "estado_desconocido"
        if p != DE_PIE:
            print("  no esta de pie: primero se levanta")
            self.levantar()
        self._llamar("Hello", {DE_PIE})
        self._dormir("espera_hello_s")
        return "ok"

    def sentar(self) -> str:
        p = self.postura()
        if p == SENTADO:
            return "ya_sentado"
        if p == TUMBADO:
            return "tumbado_levantate_primero"
        if p == DESCONOCIDO:
            return "estado_desconocido"
        self._llamar("Sit", {DE_PIE})
        try:
            self._esperar_estado(SENTADO, 0.0, float(self.c["timeout_sit_s"]))
        except Abortar as e:
            if str(e) == "interrumpido":
                raise
            return "sentado_sin_confirmar"
        return "ok"


MENSAJES = {
    "sentado_sin_confirmar": ("Sit enviado y el sensor no lo confirma. Si esta "
                              "sentado, NO salgas: revisa con 'estado'"),
    "sin_estado_usa_el_mando": ("sin estado del robot: no envio nada. "
                                "USA EL MANDO (L2+B)"),
    "no_de_pie_tras_risesit_usa_el_mando": ("tras RiseSit no se confirma de pie: "
                                            "no envio StandDown. USA EL MANDO (L2+B)"),
    "ya_de_pie": "ya esta de pie: no hago nada",
    "ya_tumbado": "ya esta tumbado: no hago nada",
    "ya_sentado": "ya esta sentado: no hago nada",
    "tumbado_levantate_primero": "esta tumbado: para sentarse, primero 'levantate'",
    "estado_desconocido": "estado DESCONOCIDO (no llega rt/sportmodestate o no cuadra): no hago nada",
}


def ejecutar(orden: str, robot: Robot) -> str:
    """Ejecuta una orden de movimiento. Devuelve el resultado a registrar."""
    accion = {"levantar": robot.levantar, "tumbar": robot.tumbar,
              "saludar": robot.saludar, "sentar": robot.sentar}[orden]
    # Solo una orden que termina sin cortarse (devuelve su resultado) deja de
    # contar como "a medias". Si se corta (Abortar: Ctrl-C, fallo, estado
    # incompatible a mitad) o salta otra excepcion, se queda activo y el
    # cierre sera el seguro.
    robot.en_accion = True
    try:
        r = accion()
        robot.en_accion = False
        return r
    except Abortar as e:
        r = str(e)
        # Se quedo a medias al levantarse (no llego a estar estable): al suelo.
        # Si fue Ctrl-C, el cierre de main() ya lo tumba.
        if orden in ("levantar", "saludar") and r.startswith("no_"):
            print(f"  [!] {r}: lo tumbo por seguridad")
            try:
                robot.tumbar(forzar=True)
            except Abortar as e2:
                r += f"+tumbar:{e2}"
        return r


def _texto_postura(robot: Robot) -> str:
    p = robot.postura()
    if p == DE_PIE:
        return "DE PIE"
    if p == TUMBADO:
        return "TUMBADO"
    if p == SENTADO:
        return "SENTADO"
    if robot._datos_frescos():
        return "DUDOSO (altura entre rangos). Compruebalo; si hace falta, USA EL MANDO (L2+B)"
    return "DESCONOCIDO (sin datos del robot). USA EL MANDO (L2+B)"


def cerrar(robot: Robot, error: bool = False) -> str:
    """Cierre al salir (salir, q, Ctrl-D, Ctrl-C o error). Devuelve la
    postura final en texto, que se imprime y se registra.

        sin datos                              -> nada; usar el mando
        tumbado                                -> nada
        orden a medias o error                 -> cierre seguro (tumbar)
        DE_PIE estable (estable_s)             -> StopMove y SE QUEDA DE PIE
        sentado, dudoso o de pie no estable    -> cierre seguro (tumbar)

    El cierre seguro es tumbar(forzar=True): StandDown solo con DE_PIE
    confirmado; sentado o dudoso, antes RiseSit; si no llega a de pie, no se
    envia StandDown.
    """
    robot.llamadas = []
    p = robot.postura()
    if p == DESCONOCIDO and not robot._datos_frescos():
        print(f"  {MENSAJES['sin_estado_usa_el_mando']}")
    elif p == TUMBADO:
        print("  ya esta tumbado: no envio nada")
    elif not robot.en_accion and not error and robot.de_pie_estable():
        print("  de pie y estable: StopMove y se queda de pie (sin StandDown)")
        try:
            robot._llamar("StopMove", {DE_PIE})
        except Abortar as e:
            print(f"  [!] {e}: no se ha podido enviar StopMove. Si hace falta: L2+B")
    else:
        motivo = ("habia una orden a medias" if robot.en_accion else
                  "ha habido un error" if error else
                  f"esta {p.lower()}" if p != DE_PIE else "de pie pero no estable")
        print(f"  {motivo}: cierre seguro (tumbar)")
        try:
            r = robot.tumbar(forzar=True)
            print(f"  {MENSAJES.get(r, r)}")
        except Abortar as e:
            print(f"  [!] {e}. Comprueba el robot. Si hace falta: L2+B")
        except Exception as e:
            print(f"  [!] fallo al tumbar: {e}. USA EL MANDO: L2+B")
    final = _texto_postura(robot)
    llamadas = "+".join(robot.llamadas) or "ninguna"
    print(f"\n>>> EL ROBOT QUEDA: {final}   (llamadas del cierre: {llamadas})")
    return final


def descartar_lo_tecleado() -> bool:
    """Vacia lo escrito mientras el robot actuaba. True si habia algo."""
    if not sys.stdin.isatty():
        return False
    import select
    import termios
    habia = bool(select.select([sys.stdin], [], [], 0)[0])
    termios.tcflush(sys.stdin, termios.TCIFLUSH)
    return habia


def _parar_handler(*_) -> None:
    PARAR["flag"] = True


# ===========================================================================
def main() -> int:
    import yaml

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["real"], default="real",
                   help="solo robot real: unitree_mujoco no emula Sport Mode")
    p.add_argument("--iface", default=None)
    p.add_argument("--config", default=str(UC / "configs" / "text_commands.yaml"))
    p.add_argument("--dry-run", action="store_true",
                   help="no crea SportClient: imprime lo que haria y simula el estado")
    p.add_argument("--log", action="store_true", help="registrar las ordenes")
    p.add_argument("--estado", action="store_true",
                   help="solo lectura: muestra rt/sportmodestate 10 s y sale")
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    c = ct.load_contract(paths.CONTRACT)
    dds = c["dds"][args.mode]
    iface = args.iface or paths.iface() or dds["interface"]
    domain = paths.domain() if paths.domain() is not None else dds["domain_id"]
    acc, ce = cfg["acciones"], cfg["estado_robot"]

    print("uc10 — ordenes escritas")
    print(f"  modo    : {args.mode} (domain {domain}, {iface})")
    if args.dry_run:
        print("  DRY RUN: no se crea SportClient ni se envia nada; estado simulado")
    print()

    if args.dry_run and not args.estado:
        estado = uc09.EstadoSimulado(ce.get("simulado_inicial", TUMBADO))
    else:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        ChannelFactoryInitialize(domain, iface)
        estado = EstadoPostura(ce, float(cfg["seguridad"]["estado_max_edad_s"]))

    if args.estado:
        print("Pon el robot de pie, SENTADO y tumbado con el mando.")
        try:
            return uc09.modo_estado(estado)
        except KeyboardInterrupt:
            return 0

    if not args.dry_run:
        print("  ROBOT FISICO. Confirma:")
        print("   - docs/SAFETY.md leido")
        print("   - espacio libre de 3 m alrededor")
        print("   - mando en la mano (L2+B para el robot)")
        print("   - estado_robot calibrado con --estado")
        if input("\n  Escribe 'si' para continuar: ").strip().lower() != "si":
            return 1
        if not estado.esperar_primero(2.0):
            print("No llega rt/sportmodestate: sin estado no se mueve el robot.")
            return 1

    filas: list = []
    ordenes: Counter = Counter()
    resultados: Counter = Counter()
    t0 = time.monotonic()
    codigo = 0
    robot = None
    postura_final = ""

    try:
        salida = SalidaNula() if args.dry_run else SalidaRobot()
        robot = Robot(salida, estado, acc, ce)
        print(f"salida: {salida.nombre}")
        print(f"estado inicial: {robot.postura()}\n")
        print(AYUDA)
        print()

        while True:
            try:
                texto = input(f"[{robot.postura()}] orden> ")
            except EOFError:
                texto = "salir"          # Ctrl-D o fin de la entrada
                print()
            # Ctrl-C en el prompt sale como KeyboardInterrupt: lo recoge el
            # except de abajo y se pasa al cierre.
            orden = interpretar(texto)
            if not texto.strip():
                continue

            t_ini = time.monotonic()
            antes = robot.postura()
            robot.llamadas = []
            resultado = ""

            if orden is None:
                resultado = "no_entendida"
                print(f"  No entiendo \"{texto.strip()}\". Escribe 'ayuda'.")
            elif orden == "ayuda":
                resultado = "ok"
                print(AYUDA)
            elif orden == "estado":
                resultado = "ok"
                mode, altura, edad = estado.leer()
                extra = "" if mode is None else (
                    f"  (mode {mode}, body_height {altura:.3f} m, "
                    f"hace {edad * 1000:.0f} ms)")
                print(f"  {antes}{extra}")
            elif orden == "salir":
                resultado = "ok"
            else:
                # Una accion a la vez: el bucle no vuelve al prompt hasta que
                # acaba. Ctrl-C durante la accion corta las esperas.
                PARAR["flag"] = False
                signal.signal(signal.SIGINT, _parar_handler)
                try:
                    resultado = ejecutar(orden, robot)
                finally:
                    signal.signal(signal.SIGINT, signal.default_int_handler)
                print(f"  {MENSAJES.get(resultado, resultado)}")
                if descartar_lo_tecleado():
                    print("  (se ha ignorado lo escrito mientras actuaba)")

            ordenes[orden or "no_entendida"] += 1
            resultados[resultado] += 1
            mode, altura, _ = estado.leer()
            filas.append([f"{t_ini - t0:.3f}", texto.strip(), orden or "",
                          antes, "+".join(robot.llamadas), resultado,
                          robot.postura(), f"{time.monotonic() - t_ini:.2f}",
                          "" if mode is None else mode,
                          "" if altura is None else f"{altura:.3f}"])

            if orden == "salir" or PARAR["flag"]:
                break

    except KeyboardInterrupt:
        print("\nCtrl-C")
    except Exception as ex:
        print(f"\n[ERROR] {type(ex).__name__}: {ex}")
        codigo = 1
    finally:
        # Un segundo Ctrl-C no debe cortar el cierre.
        signal.signal(signal.SIGINT, lambda *_: print("\n  ya estoy parando..."))
        print("\nparando...")
        if robot is not None:
            try:
                postura_final = cerrar(robot, error=codigo != 0)
            except Exception as e:
                print(f"  [!] fallo en el cierre: {e}. USA EL MANDO: L2+B")
        else:
            print("\n>>> no se ha llegado a crear la salida: no se ha enviado nada")
        signal.signal(signal.SIGINT, signal.default_int_handler)

    dur = time.monotonic() - t0
    print(f"duracion {dur:.1f} s | ordenes {dict(ordenes) or '-'}")

    if args.log and filas:
        sha = git_sha()
        destino = (paths.EXPERIMENTS / "uc10_text_commands"
                   / f"{datetime.now():%Y-%m-%dT%H%M%S}_{sha}_text")
        destino.mkdir(parents=True, exist_ok=True)
        with open(destino / "metrics.csv", "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["t", "texto", "orden", "estado_antes", "llamadas",
                        "resultado", "estado_despues", "duracion_s", "mode",
                        "body_height"])
            w.writerows(filas)
        (destino / "manifest.json").write_text(json.dumps({
            "usecase": "uc10_text_commands",
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "git_sha": sha,
            "git": {"sha": git_sha(short=False), "dirty": git_dirty()},
            "modo": args.mode,
            "dry_run": args.dry_run,
            "config": cfg,
            "duracion_s": round(dur, 1),
            "ordenes": dict(ordenes),
            "resultados": dict(resultados),
            "postura_final": postura_final,
        }, indent=2, ensure_ascii=False))
        print(f"\nescrito: {destino}")

    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
