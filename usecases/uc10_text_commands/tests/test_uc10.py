"""usecases/uc10_text_commands/tests/test_uc10.py

Pruebas de uc10 SIN ROBOT: SportClient falso que registra cada llamada y
cambia body_height como el robot, estado del robot falso (mismos rangos que
el yaml) y teclado falso. El bucle principal se prueba por el camino REAL
(sin --dry-run), con el "si" contestado por la prueba. No abre DDS. Lo que
escribe va a un directorio temporal, nunca al repo.

    1. ordenes: texto -> orden (tildes, mayusculas, atajos)
    2. cierre (cerrar) en cada estado:
         de pie estable -> StopMove y se queda DE PIE (sin StandDown)
         de pie no estable, sentado, dudoso, orden a medias, error -> tumbado
         RiseSit sin efecto -> sin StandDown; tumbado y sin datos -> nada
    3. main(): salir / q / Ctrl-D / Ctrl-C en el prompt de pie -> de pie;
       Ctrl-C a mitad de 'levantate' y de 'saluda' -> tumbado; sentado ->
       tumbado; error -> tumbado; mensaje final; --dry-run; --log
    En todas las ejecuciones: StandDown solo con DE_PIE confirmado.

Uso, dentro de la caja (no necesita robot, ~30 s):

    python3 usecases/uc10_text_commands/tests/test_uc10.py

O desde fuera, sin red y con el repo en solo lectura:

    docker run --rm --network none -v "$PWD":/workspace:ro -w /workspace \\
        -e PYTHONDONTWRITEBYTECODE=1 ghcr.io/isoto-mondragon/go2-workspace:latest \\
        python3 usecases/uc10_text_commands/tests/test_uc10.py

Sale con codigo 0 si todo pasa y 1 si algo falla.
"""
from __future__ import annotations

import builtins
import copy
import io
import json
import sys
import tempfile
import threading
import time
import types
from contextlib import redirect_stdout
from pathlib import Path

import yaml

UC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UC / "deploy"))
sys.path.insert(0, str(UC.parents[1] / "src"))

TMP = Path(tempfile.mkdtemp(prefix="uc10_tests_"))
CFG = yaml.safe_load((UC / "configs" / "text_commands.yaml").read_text())

FALLOS: list[str] = []
N_OK = 0


def check(cond: bool, nombre: str) -> None:
    global N_OK
    if cond:
        N_OK += 1
    else:
        FALLOS.append(nombre)
        print(f"  FALLO: {nombre}")


# ---------------------------------------------------------------------------
# SportClient y DDS falsos (antes de importar el script)
# ---------------------------------------------------------------------------
REG: list[tuple[float, str, float]] = []      # (t, llamada, body_height antes)
N_SPORT = {"n": 0}
LO, HI = CFG["estado_robot"]["rangos_altura"]["de_pie"]


class SportFalso:
    altura = 0.07
    sin_datos = False
    oscilar = False         # de pie "no estable": alterna 0.31 / 0.275
    risesit_falla = False   # RiseSit sin efecto (sigue sentado)

    def __init__(self):
        N_SPORT["n"] += 1

    def SetTimeout(self, t): pass
    def Init(self): pass

    def _r(self, nombre):
        REG.append((time.monotonic(), nombre, SportFalso.altura))

    def StandUp(self): self._r("StandUp"); SportFalso.altura = 0.31; return 0
    def BalanceStand(self): self._r("BalanceStand"); return 0
    def StopMove(self): self._r("StopMove"); SportFalso.oscilar = False; return 0
    def StandDown(self): self._r("StandDown"); SportFalso.altura = 0.07; return 0
    def Hello(self): self._r("Hello"); return 0
    def Sit(self): self._r("Sit"); SportFalso.altura = 0.23; return 0

    def RiseSit(self):
        self._r("RiseSit")
        SportFalso.oscilar = False
        if not SportFalso.risesit_falla:
            SportFalso.altura = 0.31
        return 0


mod = types.ModuleType("unitree_sdk2py.go2.sport.sport_client")
mod.SportClient = SportFalso
sys.modules["unitree_sdk2py.go2.sport.sport_client"] = mod
canal = types.ModuleType("unitree_sdk2py.core.channel")
canal.ChannelFactoryInitialize = lambda *a, **k: None
sys.modules["unitree_sdk2py.core.channel"] = canal

import text_commands as TC  # noqa: E402


class EstadoFalso:
    """Como EstadoPostura (rt/sportmodestate) pero leyendo de SportFalso."""

    def __init__(self, cfg, max_edad_s=0.5):
        self.cfg, self.max_edad_s, self.n = cfg, max_edad_s, 1

    def leer(self):
        if SportFalso.sin_datos:
            return None, None, float("inf")
        h = SportFalso.altura
        if SportFalso.oscilar and int(time.monotonic() / 0.15) % 2:
            h = 0.275                         # entre sentado y de pie: dudoso
        return 0, h, 0.01

    def estado(self):
        m, h, e = self.leer()
        if h is None:
            return TC.DESCONOCIDO
        return TC.clasificar_postura(m, h, self.cfg)

    def datos_frescos(self):
        return not SportFalso.sin_datos

    def esperar_primero(self, t):
        return not SportFalso.sin_datos


TC.EstadoPostura = EstadoFalso

ACC = dict(CFG["acciones"], espera_standup_s=0.2, espera_balance_s=0.1, espera_hello_s=0.2,
           espera_stopmove_s=0.05, estable_s=0.3, timeout_estable_s=1.5,
           timeout_alto_s=1.0, timeout_sit_s=1.0, timeout_standdown_s=1.0,
           timeout_estable_cierre_s=1.0)
CE = CFG["estado_robot"]


def reiniciar(altura, **flags):
    SportFalso.altura = altura
    SportFalso.sin_datos = flags.get("sin_datos", False)
    SportFalso.oscilar = flags.get("oscilar", False)
    SportFalso.risesit_falla = flags.get("risesit_falla", False)
    REG.clear()
    N_SPORT["n"] = 0
    TC.PARAR["flag"] = False


def standdown_seguro():
    """StandDown solo con body_height en el rango de pie."""
    for _, n, h in REG:
        if n == "StandDown":
            check(LO <= h <= HI, f"StandDown con body_height {h:.3f} (no de pie)")


def nombres():
    return [n for _, n, _ in REG]


# ===========================================================================
print("\n== 1. ordenes ==")
for texto, orden in (("Levántate", "levantar"), ("ARRIBA", "levantar"), ("l", "levantar"),
                     ("túmbate", "tumbar"), ("t", "tumbar"), ("¡Hola!", "saludar"),
                     ("siéntate", "sentar"), ("estado", "estado"), ("q", "salir"),
                     ("salir", "salir"), ("baila", None), ("", None)):
    check(TC.interpretar(texto) == orden, f"'{texto}' -> {TC.interpretar(texto)} (esperado {orden})")
check("de pie se queda de pie" in TC.AYUDA, "la ayuda de 'salir' no explica el cierre nuevo")
print("  tildes, mayusculas, signos y atajos; la ayuda explica el cierre")


# ===========================================================================
print("\n== 2. cierre en cada estado ==")


def cierre(altura, en_accion=False, error=False, **flags):
    reiniciar(altura, **flags)
    with redirect_stdout(io.StringIO()):
        rb = TC.Robot(TC.SalidaRobot(), EstadoFalso(CE), ACC, CE)
    rb.en_accion = en_accion
    buf = io.StringIO()
    with redirect_stdout(buf):
        final = rb and TC.cerrar(rb, error=error)
    standdown_seguro()
    return final, nombres(), buf.getvalue()


CASOS = [
    # (nombre, altura, kwargs, llamadas esperadas, postura final empieza por)
    ("de pie estable", 0.31, {}, ["StopMove"], "DE PIE"),
    ("de pie NO estable", 0.31, {"oscilar": True}, None, "TUMBADO"),
    ("sentado", 0.23, {}, ["RiseSit", "StopMove", "StandDown"], "TUMBADO"),
    ("dudoso", 0.275, {}, ["RiseSit", "StopMove", "StandDown"], "TUMBADO"),
    ("sentado, RiseSit sin efecto", 0.23, {"risesit_falla": True}, ["RiseSit"], "SENTADO"),
    ("de pie con orden a medias", 0.31, {"en_accion": True}, ["StopMove", "StandDown"], "TUMBADO"),
    ("de pie con error", 0.31, {"error": True}, ["StopMove", "StandDown"], "TUMBADO"),
    ("tumbado", 0.07, {}, [], "TUMBADO"),
    ("sin datos", 0.31, {"sin_datos": True}, [], "DESCONOCIDO"),
]
for nombre, h, kw, esperadas, fin in CASOS:
    en_accion = kw.pop("en_accion", False)
    error = kw.pop("error", False)
    final, ll, out = cierre(h, en_accion, error, **kw)
    if esperadas is None:
        check("StandDown" in ll and ll[-1] == "StandDown", f"{nombre}: {ll}")
    else:
        check(ll == esperadas, f"{nombre}: llamadas {ll} (esperado {esperadas})")
    check(final.startswith(fin), f"{nombre}: queda {final!r} (esperado {fin})")
    check(f">>> EL ROBOT QUEDA: {fin}" in out, f"{nombre}: el mensaje final no dice {fin}")
    if nombre in ("sin datos", "sentado, RiseSit sin efecto"):
        check("MANDO" in out, f"{nombre}: no pide usar el mando")
    print(f"  {nombre:<28} -> {ll or 'nada'}; queda {final.split('.')[0]}")


# ===========================================================================
print("\n== 3. main() por el camino real con robot falso ==")
TC.paths.EXPERIMENTS = TMP / "experiments"


def teclado(entradas, al_devolver=None):
    """input() falso: devuelve textos o lanza excepciones (KeyboardInterrupt,
    EOFError, RuntimeError). Al acabarse, Ctrl-D. al_devolver[texto] se
    ejecuta justo al devolver ese texto (p. ej. un Ctrl-C a mitad)."""
    cola = list(entradas)

    def leer(*_):
        if not cola:
            raise EOFError
        x = cola.pop(0)
        if isinstance(x, type) and issubclass(x, BaseException):
            raise x()
        if al_devolver and x in al_devolver:
            al_devolver[x]()
        return x
    return leer


def ctrl_c_en(segundos):
    """Lo mismo que hace el manejador de SIGINT durante una orden."""
    return lambda: threading.Timer(segundos, lambda: TC.PARAR.__setitem__("flag", True)).start()


def ejecutar_main(entradas, altura=0.07, acc=None, extra=(), al_devolver=None):
    reiniciar(altura)
    cfg = copy.deepcopy(CFG)
    cfg["acciones"].update(ACC)
    cfg["acciones"].update(acc or {})
    ruta = TMP / "cfg.yaml"
    ruta.write_text(yaml.safe_dump(cfg))
    sys.argv = ["text_commands.py", "--mode", "real", "--iface", "lo", "--config", str(ruta),
                *extra]
    entrada = builtins.input
    builtins.input = teclado(["si", *entradas], al_devolver)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            codigo = TC.main()
    finally:
        builtins.input = entrada
        TC.PARAR["flag"] = False
    standdown_seguro()
    out = buf.getvalue()
    check("Traceback" not in out, f"traza en la salida: {out[-300:]}")
    return codigo, out, nombres()


def final_de(out):
    linea = next((l for l in out.splitlines() if l.startswith(">>> EL ROBOT QUEDA:")), "")
    return linea.split(":", 1)[1].strip() if linea else "(sin mensaje final)"


ESCENARIOS = [
    # (nombre, entradas, kwargs, postura final, comprobacion de llamadas)
    ("levantate + salir", ["l", "salir"], {},
     "DE PIE", lambda ll: ll[-1] == "StopMove" and "StandDown" not in ll),
    ("levantate + q", ["levantate", "q"], {},
     "DE PIE", lambda ll: ll[-1] == "StopMove" and "StandDown" not in ll),
    ("levantate + Ctrl-D", ["arriba", EOFError], {},
     "DE PIE", lambda ll: ll[-1] == "StopMove" and "StandDown" not in ll),
    ("levantate + Ctrl-C en el prompt", ["l", KeyboardInterrupt], {},
     "DE PIE", lambda ll: ll[-1] == "StopMove" and "StandDown" not in ll),
    ("de pie al arrancar + saluda + salir", ["h", "salir"], {"altura": 0.31},
     "DE PIE", lambda ll: ll == ["Hello", "StopMove"]),
    ("Ctrl-C a mitad de levantate", ["l"],
     {"acc": {"espera_standup_s": 1.0}, "al_devolver": {"l": ctrl_c_en(0.3)}},
     "TUMBADO", lambda ll: ll == ["StandUp", "StopMove", "StandDown"]),
    ("Ctrl-C a mitad de saluda", ["h"],
     {"altura": 0.31, "acc": {"espera_hello_s": 1.0}, "al_devolver": {"h": ctrl_c_en(0.3)}},
     "TUMBADO", lambda ll: ll == ["Hello", "StopMove", "StandDown"]),
    ("sentado + salir", ["l", "s", "salir"], {},
     "TUMBADO", lambda ll: ll[-4:] == ["Sit", "RiseSit", "StopMove", "StandDown"]),
    ("error inesperado de pie", ["l", RuntimeError], {},
     "TUMBADO", lambda ll: ll[-2:] == ["StopMove", "StandDown"]),
    ("tumbado + salir", ["salir"], {},
     "TUMBADO", lambda ll: ll == []),
]
for nombre, entradas, kw, fin, ok_llamadas in ESCENARIOS:
    codigo, out, ll = ejecutar_main(entradas, **kw)
    check(final_de(out).startswith(fin), f"{nombre}: queda {final_de(out)!r} (esperado {fin})")
    check(ok_llamadas(ll), f"{nombre}: llamadas {ll}")
    check(codigo == (1 if nombre.startswith("error") else 0), f"{nombre}: codigo {codigo}")
    print(f"  {nombre:<38} -> queda {fin:<7} {ll}")

# --dry-run: ningun SportClient; de pie se queda de pie.
reiniciar(0.07)
sys.argv = ["text_commands.py", "--dry-run", "--config", str(TMP / "cfg.yaml")]
entrada = builtins.input
builtins.input = teclado(["l", "salir"])
buf = io.StringIO()
try:
    with redirect_stdout(buf):
        codigo = TC.main()
finally:
    builtins.input = entrada
out = buf.getvalue()
check(N_SPORT["n"] == 0 and not REG, "dry-run: crea SportClient o envia algo")
check(final_de(out).startswith("DE PIE") and "haria: SportClient.StopMove()" in out
      and "haria: SportClient.StandDown" not in out, "dry-run: cierre")
print("  --dry-run: 0 SportClient; levantate + salir -> haria StopMove y queda DE PIE")

# --log: postura final en el manifest.
codigo, out, ll = ejecutar_main(["l", "salir"], extra=("--log",))
runs = sorted((TMP / "experiments" / "uc10_text_commands").glob("*"))
man = json.loads((runs[-1] / "manifest.json").read_text()) if runs else {}
check(man.get("postura_final") == "DE PIE", f"--log: postura_final {man.get('postura_final')!r}")
print(f"  --log: manifest.postura_final = {man.get('postura_final')!r}")

print(f"\n{N_OK} comprobaciones OK, {len(FALLOS)} fallos")
sys.exit(1 if FALLOS else 0)
