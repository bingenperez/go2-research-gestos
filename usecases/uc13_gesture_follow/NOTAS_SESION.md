# Notas de sesión — uc13 (y uc10, uc12) — 2026-11-20

Para continuar en una sesión nueva sin perder nada. Fuentes: los README de
cada caso de uso, `configs/gesture_follow.yaml` y los logs de `experiments/`.

> **Nada de esto está en git.** Todos los archivos de uc10, uc12 y uc13 (y los
> logs de `experiments/`) están *untracked*. Ver el apartado 5.

---

## 1. Estado de uc13 (gestos + seguimiento a 2 m)

**Qué hace:** cámara del robot → YOLO (persona: `lateral`, `tam` → metros) y
MediaPipe Pose (gestos). Una mano: levantarse. Dos manos: empezar o dejar de
seguir. T: saludar (quieto) o parar (siguiendo). Brazos cruzados: tumbarse.
Sigue a ~2 m con `Move` (histéresis 2.15/2.5 m, vx 0.25–0.4, marcha atrás
limitada a −0.15 m/s, como mucho 3 s). En reposo inclina el cuerpo 0.25 rad
con `Euler` para ver los gestos. Detalle completo en `README.md`.

| | estado |
|---|---|
| Lógica completa | probada sin robot: `tests/test_uc13.py`, **20 216 comprobaciones, 0 fallos** (Docker sin red, repo en solo lectura) |
| `--estado` (rt/sportmodestate) | probado en el robot real (según el usuario). `mode` siempre 0: la postura se decide por `body_height` |
| `--dry-run` con seguimiento, cámara del robot | probado en el robot real (según el usuario; sin `--log`, no hay registro) |
| `--probar-inclinacion` A y B | **probadas en el robot real con `--log`: funcionan** (apartado 2) |
| Prueba C (inclinarse tras caminar) | **PENDIENTE** (hace falta el mando) |
| Prueba "primera vez" (recién encendido o tras un rato) | **PENDIENTE** |
| Prueba real completa (`--source robot --show --log`, sin dry-run) | **PENDIENTE**. Nunca se ha hecho con la inclinación activa |
| Calibración de `tam` (2.0 m → 0.786, 1.3 m → 0.888) | hecha el 2026-11-18 |
| Marcha atrás a 0.15 m/s | sin comprobar (Sport Mode puede ignorar < ~0.2 m/s) |

---

## 2. Inclinación

**Configuración** (`configs/gesture_follow.yaml`, bloque `inclinacion:`):

| clave | valor | por qué |
|---|---|---|
| `activa` | `true` | |
| `signo` | **`-1`** | pitch negativo = morro ARRIBA. Con +0.25 el morro bajó y con −0.25 subió (2026-11-20). Confirmado |
| `max_rad` | 0.25 | ~14.3° pedidos; el robot se inclina ~12.4° |
| `periodo_envio_s` | 0.1 | Euler se reenvía cada 0.1 s. Un solo Euler no mueve el robot |
| `t_inclinar_s` | 0.5 | medido: 90 % a 0.31 s |
| `t_enderezar_s` | **0.7** (antes 0.5) | medido: < 2° a 0.27 s, asentado a ~0.4 s. Margen |
| `espera_balance_s` | **1.0** (nuevo) | espera mínima tras cualquier `BalanceStand` antes del primer Euler: la de todas las pruebas que funcionaron |
| `espera_quieto_s` | 1.0 | en reposo este tiempo antes de inclinarse |

**`estado_robot.de_pie_activo` = [0.26, 0.40]** (antes 0.36). Inclinado, el
`body_height` sube a 0.357–0.366 m. Con 0.36 el robot inclinado se habría
visto DUDOSO: Euler rechazado y, siguiendo, dejaría de seguir.

**Método:** en reposo (QUIETO de pie, o SIGUIENDO con vx = 0 y wz ≈ 0 más de
1 s), `BalanceStand`, esperar 1 s y `Euler(0, −0.25, 0)` cada 0.1 s mientras
dure. Antes de cualquier `Move`, Euler 0 cada 0.1 s durante 0.7 s. Solo
tiempos: la IMU no decide nada, solo se muestra.

**Pruebas en el robot real (`--probar-inclinacion`)**. Siempre: códigos 0,
`MotionSwitcher.CheckMode` = `mcf`, sport API 1.0.0.1, `mode` 0.

| prueba | signo | resultado | log |
|---|---|---|---|
| 1, justo tras **encender** | +1 | **no se movió** (IMU −0.0°) | no |
| 2, seguida de la 1 | +1 | se movió, **morro abajo**; IMU +13.0°; body_height 0.328→0.366 | no |
| 3, **tras un rato** sin usarlo | −1 | **no se movió** (IMU −0.0°, body_height 0.320), códigos 0 | no |
| 4, seguida de la 3 | −1 | **morro arriba**; IMU −12.9°; body_height 0.320→0.361 | no |
| A, `--mantener 15 --ciclos 2` | −1 | **se movió a la primera**, los dos ciclos | `experiments/uc13_gesture_follow/2026-11-20T100435_30eb55f_probar_inclinacion/` |
| B, justo después, `--mantener 3` | −1 | **se movió a la primera** | `…/2026-11-20T100558_30eb55f_probar_inclinacion/` |

**Tiempos medidos (A y B, IMU de rt/lowstate a ~49 Hz):** empieza a moverse
0.16 s tras el primer Euler; 90 % a 0.31 s; estable en −13.4° de IMU (base
≈ −1°, o sea ≈ 12.4° de inclinación), ±0.03°; vuelve a < 2° 0.27 s tras el
Euler 0 y se asienta a ~0.4 s. Las dos IMU (lowstate y sportmodestate) miden
bien. El 0.0 de antes era porque el robot no se había movido.

**Dos fallos sin explicar** (pruebas 1 y 3): la primera secuencia tras encender
o tras un rato no hizo nada, aunque todo devolvió 0, y la siguiente sí.
Ninguno de los dos tiene log. Con A y B no se reprodujo, pero no se sabe
cuánto tiempo llevaba el robot sin órdenes antes de A.

Hipótesis (ninguna confirmada):

| | hipótesis | cómo se distingue |
|---|---|---|
| A | desde reposo, `BalanceStand` tarda varios segundos en tener efecto (servicio `mcf`) | el ciclo 1 se mueve tarde (mirar "empieza X s después del BalanceStand") |
| B | la primera secuencia de Euler "arma" algo y solo funciona la siguiente | el ciclo 1 no se mueve en 15 s y el ciclo 2 sí (sale `PATRON`) |
| C | el robot entra en algún estado de reposo tras un rato y las primeras órdenes lo despiertan | como A: depende del tiempo, no del número de secuencias |

**Efecto en el programa principal:** si pasa, esa vez el robot no mira hacia
arriba. No es peligroso, porque no se anda inclinado y Move va por tiempo.
Con B, en QUIETO no se inclinaría hasta la siguiente acción. Si también pasa
**tras caminar** (prueba C), afectaría a cada parada.

**Cambios en el programa principal hoy, tras A y B:**
1. Espera de 1 s tras cualquier `BalanceStand` antes del primer Euler
   (`espera_balance_s`). Antes, en QUIETO, el primer Euler salía en el mismo
   ciclo que el `BalanceStand`, y eso nunca se había probado en el robot.
2. `t_enderezar_s` de 0.5 a 0.7 s.
3. **Corrección encontrada por las pruebas:** al empezar a seguir, no se
   inclina hasta 1 s después, para medir antes la distancia recto. Con la
   espera de 1 s, el gesto "dos manos" podía confirmarse con el robot aún
   recto. Entonces se inclinaba sin haber medido, y con la persona a 1.1 m no
   retrocedía.

`--probar-inclinacion` ahora: diagnóstico de solo lectura (versión del API,
`CheckMode`), código y duración de cada llamada, la IMU registrada a ~50 Hz,
latencia, 90 % y vuelta por ciclo, y la marca `PATRON`. Opciones:
`--mantener S`, `--espera-balance S` (por defecto `espera_balance_s` del yaml),
`--ciclos N` (sin nuevo BalanceStand entre ciclos) y `--log` (`serie.csv` +
`manifest.json`).

---

## 3. uc10 (órdenes escritas): cierre nuevo

**Cambio de hoy:** al salir (`salir`, `q`, Ctrl-D, Ctrl-C o error), en
`deploy/text_commands.py`, función `cerrar()`:

| estado al salir | qué hace | queda |
|---|---|---|
| sin datos del robot | nada; pide usar el mando | DESCONOCIDO |
| tumbado | nada | tumbado |
| orden a medias (Ctrl-C durante una orden, o una orden cortada) o error | cierre seguro (tumbar) | tumbado |
| **de pie y estable** (de pie 1 s seguido; espera como mucho 3 s, `timeout_estable_cierre_s`) | **solo `StopMove`, sin `StandDown`** | **de pie** |
| de pie no estable, sentado o dudoso | cierre seguro (`RiseSit` si hace falta; sin `StandDown` si no llega a de pie) | tumbado |

Siempre imprime `>>> EL ROBOT QUEDA: …`. Con `--log`, `manifest.json` incluye
`postura_final`.

- **Tests nuevos:** `tests/test_uc10.py` (antes uc10 no tenía), **95
  comprobaciones, 0 fallos**. Cubre el cierre en 9 estados, 10 recorridos del
  bucle con teclado falso, `--dry-run` y `--log`, y que nunca hay `StandDown`
  sin estar de pie.
- **En el robot real:** falta confirmarlo a la vista. Pero `experiments/uc10_text_commands/`
  tiene 4 ejecuciones **reales** (no dry-run) con el código nuevo, todas con
  `postura_final: DE PIE`: `2026-11-20T091854`, `T094712`, `T094745` y
  `T110536` (hora del contenedor). La de `T085841` es anterior al cambio
  (sin `postura_final`).
- uc10 **importa uc09** (`gesture_control.py`). uc09 no se ha tocado.

---

## 4. uc12 (gestos + Hello): probado en el robot real hoy, funciona

Tres ejecuciones reales con la cámara del robot en `experiments/uc12_gesture_hello/`:
- `2026-11-20T090212`: levantar 2, saludar 2, tumbar 2;
- `T093258`: saludar 4, tumbar 1, levantar 1;
- `T101004`: saludar 1, tumbar 1, levantar 1.

No se ha cambiado nada de uc12.

---

## 5. Git y archivos tocados

**Nada está en git.** `usecases/uc09` a `uc13` y los logs de `experiments/`
están *untracked*. Además había cambios previos del usuario en archivos que sí
están en git (`docker-compose.linux.yml`, `docker-compose.linux.yml.bak`,
`docker/scene_flat.xml` y `usecases/uc01_locomotion/deploy/run_policy.py`),
que no son de esta sesión. Último commit: `30eb55f`.

Archivos tocados en esta sesión (18–20 Nov):

| caso | archivo | qué |
|---|---|---|
| uc13 | `control/inclinacion.py` | reescrito: máquina por tiempos, Euler cada 0.1 s |
| uc13 | `deploy/gesture_follow.py` | inclinación en reposo, espera tras BalanceStand, medir recto al empezar a seguir, IMU solo informativa (+ lowstate), `--probar-inclinacion` con diagnóstico y medida |
| uc13 | `configs/gesture_follow.yaml` | bloque `inclinacion` (signo −1, 0.25 rad, tiempos), `de_pie_activo` 0.40, resultados |
| uc13 | `eval/measure.py` | `pitch_cmd` en radianes, en valor absoluto |
| uc13 | `tests/test_uc13.py` | pruebas de la inclinación, invariantes, P (sonda) y Q (desactivada) |
| uc13 | `README.md` | inclinación, pruebas en el robot y sonda |
| uc13 | `NOTAS_SESION.md` | este archivo |
| uc10 | `deploy/text_commands.py` | `cerrar()`, `en_accion`, `de_pie_estable()`, ayuda de `salir` |
| uc10 | `configs/text_commands.yaml` | `timeout_estable_cierre_s: 3.0` |
| uc10 | `tests/test_uc10.py` | **nuevo** |
| uc10 | `README.md` | tabla del cierre y pruebas |

Sin tocar: uc09, uc11, uc12, y en uc13 `perception/` y `control/seguimiento.py`.

---

## 6. Comandos

### Contenedor con ventanas (en el portátil, desde la raíz del repo)

```bash
echo $DISPLAY                  # si sale vacío (p. ej. por ssh):
export DISPLAY=:0
xhost +local:docker
docker compose -f docker-compose.yml -f docker-compose.linux.yml \
    --profile real run --rm \
    -e GO2_GUI=x11 -e DISPLAY=$DISPLAY -e QT_X11_NO_MITSHM=1 \
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
    real bash
```

### MediaPipe (dentro del contenedor, cada vez: no persiste)

Solo para uc12 y para uc13 con cámara. **No** hace falta para
`--probar-inclinacion` ni para `--estado`.

```bash
pip3 install --no-cache-dir --no-deps mediapipe==1.2.3
pip3 install --no-cache-dir "absl-py~=2.3" "sounddevice~=0.5" certifi
mkdir -p /tmp/go2models
curl -fsSL -o /tmp/go2models/pose_landmarker_lite.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
```

### Pruebas sin robot (desde la raíz del repo, fuera del contenedor)

```bash
docker run --rm --network none -v "$PWD":/workspace:ro -w /workspace \
    -e PYTHONDONTWRITEBYTECODE=1 ghcr.io/isoto-mondragon/go2-workspace:latest \
    python3 usecases/uc13_gesture_follow/tests/test_uc13.py      # ~3 min, "0 fallos"
docker run --rm --network none -v "$PWD":/workspace:ro -w /workspace \
    -e PYTHONDONTWRITEBYTECODE=1 ghcr.io/isoto-mondragon/go2-workspace:latest \
    python3 usecases/uc10_text_commands/tests/test_uc10.py       # ~30 s, "0 fallos"
```

### Pruebas pendientes en el robot, en este orden

Interfaz `eno1`. En todas: mando en la mano (L2+B = parada) y escribir `si`
cuando lo pida.

**1. uc10, confirmar a la vista el cierre de pie** (rápido; los logs ya dicen
DE PIE):
```bash
python3 usecases/uc10_text_commands/deploy/text_commands.py --mode real --iface eno1 --log
#   l      -> se levanta
#   salir  -> solo "SportClient.StopMove()" y ">>> EL ROBOT QUEDA: DE PIE"; debe seguir de pie
```

**2. uc13, inclinación "primera vez".** Robot recién encendido o 10–15 min sin
órdenes, de pie:
```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --mode real --iface eno1 \
    --probar-inclinacion --mantener 15 --ciclos 2 --log
```
Mirar en `== resultado ==` si el ciclo 1 se movió y en qué segundo desde el
BalanceStand, si se movió el ciclo 2 y si aparece `PATRON`. Opcional,
otra vez desde "primera vez", con `--espera-balance 0` para saber si la espera
importa.

**3. uc13, prueba C (tras caminar).** Hace falta el mando: andar ~5 s con el
mando, parar de pie e **inmediatamente**:
```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --mode real --iface eno1 \
    --probar-inclinacion --mantener 15 --ciclos 1 --log
```

**4. uc13, dry-run con la cámara del robot** (con MediaPipe; no mueve nada).
Comprobar que en reposo diría `Euler(0, -0.250, 0)` ~1 s después del
`BalanceStand`, y que YOLO y los gestos van bien:
```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --mode real --iface eno1 \
    --source robot --dry-run --show --debug
```

**5. uc13, prueba real completa.** Empezar con el robot tumbado y el recorrido
despejado:
```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --mode real --iface eno1 \
    --source robot --show --duration 180 --log
```
Comprobar:
- una mano: se levanta y ~1 s después mira hacia arriba;
- T y cruzados a 2 m, inclinado;
- dos manos a ~2.5 m: se endereza y sigue, **nunca anda inclinado**;
- parado más de 1 s: se inclina; si te alejas, se endereza (0.7 s) y anda;
- una mano a menos de 2 m, inclinado: la ve;
- al salir: se endereza y se tumba.

Después: `python3 usecases/uc13_gesture_follow/eval/measure.py`.

Los logs de las pruebas 2 y 3 los puede leer la siguiente sesión en
`experiments/uc13_gesture_follow/*_probar_inclinacion/`.
