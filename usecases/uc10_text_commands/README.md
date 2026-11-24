# uc10 — Órdenes escritas

## Qué hace

Le das órdenes al robot real **escribiendo en la terminal**. Escribes la orden,
pulsas Enter y el robot la ejecuta con Sport Mode. No usa cámara ni voz.

teclado ──► orden ──► estado del robot ──► SportClient


| escribe | qué hace |
|---|---|
| `levántate`, `arriba`, `l` | ponerse de pie: `StandUp()` + `BalanceStand()`. Si está sentado, `RiseSit()` |
| `túmbate`, `abajo`, `t` | tumbarse como al encenderlo: `StopMove()` + `StandDown()`. Si está sentado, antes `RiseSit()` |
| `saluda`, `hola`, `h` | `Hello()`. Si no está de pie, antes se levanta y espera a estar estable |
| `siéntate`, `s` | `Sit()`, solo si está de pie |
| `estado` | dice si está de pie, sentado o tumbado (con `mode` y `body_height`) |
| `ayuda` | la lista de órdenes |
| `salir`, `q` | termina. **De pie y estable, se queda de pie** (solo `StopMove()`); si no, cierre seguro (ver Seguridad) |

Da igual mayúsculas o minúsculas, y con tilde o sin ella. Si la orden no se
entiende, o si el robot ya está en la postura pedida, no hace nada y lo dice.

## Por qué está montado así

**Solo acciones discretas de Sport Mode.** Nunca `Move`, ni políticas RL. Solo
se pueden hacer siete llamadas (`StandUp`, `BalanceStand`, `StopMove`,
`StandDown`, `Hello`, `Sit` y `RiseSit`); cualquier otra da error. No hay modo
simulación porque `unitree_mujoco` no emula Sport Mode.

**El estado del robot manda.** Se lee de `rt/sportmodestate` reutilizando
`EstadoSport` de uc09, con una postura más: sentado. Antes de cada llamada se
comprueba que es compatible. Si no llega estado reciente, el estado es
`DESCONOCIDO` y no se hace nada.

**Una orden a la vez.** Mientras el robot actúa no hay prompt. Lo que escribas
durante la acción se descarta al terminar, para que no se encadenen órdenes sin
querer.

## Cómo se ejecuta

Dentro de la caja en modo robot (GUIA 4.4). No necesita dependencias nuevas.

```bash
# 1. Sin robot: imprime lo que haria y simula el estado (empieza TUMBADO)
python3 usecases/uc10_text_commands/deploy/text_commands.py --dry-run --log

# 2. Calibrar el estado (solo lectura): con el mando, pon el robot de pie,
#    sentado y tumbado, y anota mode y body_height
python3 usecases/uc10_text_commands/deploy/text_commands.py --iface <IFACE> --estado

# 3. De verdad. Mando en la mano
python3 usecases/uc10_text_commands/deploy/text_commands.py --mode real --iface <IFACE> --log

# Resumen del ultimo experimento
python3 usecases/uc10_text_commands/eval/measure.py

# Pruebas sin robot (~30 s, "0 fallos")
python3 usecases/uc10_text_commands/tests/test_uc10.py
```

Pruebas desde fuera de la caja, sin red y con el repo en solo lectura:

```bash
docker run --rm --network none -v "$PWD":/workspace:ro -w /workspace \
    -e PYTHONDONTWRITEBYTECODE=1 ghcr.io/isoto-mondragon/go2-workspace:latest \
    python3 usecases/uc10_text_commands/tests/test_uc10.py
```

`tests/test_uc10.py` usa un SportClient y un estado del robot falsos (no abre
DDS) y comprueba:

- las órdenes;
- el cierre en cada estado: de pie estable, de pie no estable, sentado,
  dudoso, `RiseSit` sin efecto, orden a medias, error, tumbado y sin datos;
- el bucle completo con teclado falso: `salir`, `q`, Ctrl-D y Ctrl-C en el
  prompt (queda de pie); Ctrl-C a mitad de `levántate` y de `saluda`,
  sentado y error (queda tumbado);
- el mensaje final, `--dry-run` y `--log`;
- que nunca se envía `StandDown()` sin estar de pie.

Argumentos: `--mode` (solo `real`), `--iface`, `--config`, `--dry-run`, `--log`
y `--estado`.

Con `--log` se escribe `experiments/uc10_text_commands/<fecha>_<sha>_text/`.
Contiene `metrics.csv`, con una fila por orden (texto, orden, estado antes y
después, llamadas hechas, resultado, duración, `mode` y `body_height`), y
`manifest.json`.

## Seguridad

- Antes de mover el robot real hay que escribir `si`, como en uc04 y uc09. Al
  arrancar **no se levanta solo**: espera órdenes.
- **`StandDown()` solo se envía con el robot DE PIE confirmado por el sensor.**
- **Cierre** (`salir`, `q`, Ctrl-D, Ctrl-C o error; función `cerrar()`), en
  este orden:

  | estado al salir | qué hace | queda |
  |---|---|---|
  | sin datos del robot | no envía nada y pide usar el mando | DESCONOCIDO |
  | tumbado | nada | tumbado |
  | orden a medias (Ctrl-C durante una orden, o una orden cortada por un fallo) o error inesperado | cierre seguro | tumbado |
  | **de pie y estable** (de pie sin interrupción `estable_s` = 1 s; espera como mucho `timeout_estable_cierre_s` = 3 s) | **solo `StopMove()`, sin `StandDown()`** | **de pie** |
  | de pie pero no estable, sentado, o dudoso (altura entre dos rangos) | cierre seguro | tumbado |

  **Cierre seguro:** de pie, `StopMove()` + `StandDown()`. Sentado o dudoso,
  antes `RiseSit()` y esperar a estar de pie. Si no llega a estar de pie,
  **no** envía `StandDown()` y pide usar el mando. La orden `túmbate` desde
  sentado sigue el mismo camino.

  Al final siempre imprime en qué postura queda, por ejemplo
  `>>> EL ROBOT QUEDA: DE PIE   (llamadas del cierre: StopMove)`. Con `--log`
  también se guarda en `manifest.json` (`postura_final`).
- **Ctrl-C en el prompt** (sin ninguna orden en marcha) es como `salir`: de pie
  y estable, se queda de pie.
- **Ctrl-C durante una orden** corta las esperas y pasa al cierre **seguro**:
  se tumba aunque esté de pie.
- Si al levantarse no llega a estar estable, no saluda e intenta tumbarse con
  las mismas reglas.
- Si tras `Sit()` el sensor no confirma que está sentado, lo avisa
  (`sentado_sin_confirmar`). Comprueba con `estado` antes de salir.
- `--dry-run` no crea `SportClient`: solo imprime `[DRY RUN] haria: ...`.
- **La parada de emergencia es el mando: `L2` + `B`.**

## Calibración

Medido con `--estado` el 2026-11-17. **El robot da `mode 0` en todas las
posturas**, así que la postura se decide por `body_height`:

| postura | medido | rango en `text_commands.yaml` |
|---|---|---|
| tumbado | 0.070 m | 0.00 – 0.12 |
| sentado | 0.228 m | 0.20 – 0.26 |
| de pie | 0.311 – 0.320 m | 0.28 – 0.36 |

Una altura fuera de los tres rangos es `DESCONOCIDO`: son las alturas de paso
mientras cambia de postura. Si otro Go2 sí distingue posturas por `mode`, pon
`usar_mode: true` y ajusta las listas `modos_*`.

Fallo que motivó esto: con `0` en las listas de de pie y tumbado, y un único
umbral de 0.20 m, el robot sentado (0.228 m) se veía como de pie. `Sit()`
respondió `no_sentado`, y el cierre le habría lanzado `StandDown()` sentado.

## Estado

- [x] Órdenes con y sin tilde, y atajos de una letra
- [x] Estado de pie / sentado / tumbado, con las órdenes incompatibles rechazadas
- [x] Dry-run con estado simulado
- [x] Cierre: de pie y estable se queda de pie; si no, cierre seguro (probado sin robot)
- [x] Alturas medidas con `--estado` (mode siempre 0)
- [ ] Clasificación por rangos comprobada con `--estado` en las tres posturas
- [ ] Cierre de pie comprobado en el robot real (`l` y después `salir`: debe quedarse de pie)
- [ ] Probado en robot real
