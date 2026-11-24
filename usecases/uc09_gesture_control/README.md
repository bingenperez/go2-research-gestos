# uc09 — Control por gestos

## Qué hace

El robot real mira a una persona con la cámara y responde a dos gestos:

| gesto | robot de pie | robot tumbado |
|---|---|---|
| **mano levantada** (muñeca por encima de la cabeza) | `Hello()`: saluda | `StandUp()` + `BalanceStand()`, espera a estar estable, y `Hello()` |
| **brazos cruzados** delante del pecho | `StopMove()` + `StandDown()`: se tumba y se queda tumbado | nada |

cámara ──► MediaPipe Pose ──► gesto ──► confirmación ──► SportClient
                                       (~1 s y cooldown)


## Por qué está montado así

**Solo acciones discretas de Sport Mode.** Nunca `Move`, ni acrobacias, ni
políticas RL. Con eso no puede quedarse una velocidad aplicada si algo falla.
Por eso tampoco hay modo simulación: `unitree_mujoco` no emula Sport Mode.

**MediaPipe PoseLandmarker, no `mp.solutions.pose`.** La API clásica solo ve a
una persona. PoseLandmarker busca hasta `num_poses` y se usa la de mayor
tamaño, que es la más cercana: el mismo criterio que uc04 con YOLO.

**Un gesto no es un fotograma.** Para que cuente tiene que mantenerse
`frames_necesarios` fotogramas seguidos (~1 s). Después de cada acción hay un
cooldown, y hay que bajar los brazos antes de que el mismo gesto vuelva a
contar.

**El estado del robot manda.** Se lee de `rt/sportmodestate` (`mode` y
`body_height`). Antes de cada llamada se comprueba que es compatible: no se
saluda tumbado ni se levanta un robot que ya está de pie. Si no llega estado
reciente, o si `mode` y la altura se contradicen, el estado es `DESCONOCIDO` y
no se hace nada.

## Cómo se ejecuta

Todo dentro de la caja (GUIA 3.2 o 4.4). Primero hay que instalar MediaPipe
(ver más abajo).

```bash
# 1. Sin robot, con la webcam: recorre la maquina de estados entera.
#    El estado del robot es simulado y empieza TUMBADO.
python3 usecases/uc09_gesture_control/deploy/gesture_control.py \
    --source webcam --dry-run --show --log

# 2. Robot encendido. Calibrar el estado (solo lectura): ponlo de pie y
#    tumbado con el mando y anota mode y body_height.
python3 usecases/uc09_gesture_control/deploy/gesture_control.py \
    --iface <IFACE> --estado

# 3. Camara del robot, sin mover nada. Comprueba que ve tu cabeza y tus
#    manos (ver "La camara esta baja").
python3 usecases/uc09_gesture_control/deploy/gesture_control.py \
    --mode real --iface <IFACE> --source robot --show --dry-run

# 4. De verdad. Mando en la mano.
python3 usecases/uc09_gesture_control/deploy/gesture_control.py \
    --mode real --iface <IFACE> --source robot --show --duration 120 --log

# Resumen del ultimo experimento
python3 usecases/uc09_gesture_control/eval/measure.py
```

Solo la detección, sin robot ni máquina de estados:

```bash
python3 usecases/uc09_gesture_control/perception/pose.py --show
```

Los argumentos son los de `follow_person.py`: `--mode` (solo `real`),
`--iface`, `--camera`, `--source robot|webcam`, `--config`, `--show`,
`--duration`, `--dry-run` y `--log`. Hay uno más, `--estado`.

Con `--log` se escribe `experiments/uc09_gesture_control/<fecha>_<sha>_gestures/`,
con `metrics.csv` (una fila por fotograma: gesto, contador, estado del robot,
acción y resultado) y `manifest.json`.

## Seguridad

- Antes de mover el robot real hay que escribir `si`, como en uc04.
- Al arrancar, si el robot está tumbado se levanta. Si el estado es
  `DESCONOCIDO`, no se mueve nada.
- **Watchdog:** si una imagen llega más de 0.5 s después de la anterior, o tarda
  más de 0.5 s en procesarse, la racha del gesto se reinicia y no se lanza
  ninguna acción. Un fotograma idéntico al anterior (driver colgado) cuenta
  como "no ha llegado".
- Al terminar (fin, `q`, Ctrl-C o error): `StopMove()` + `StandDown()`. El robot
  queda tumbado, como pide GUIA 4.0.
- `--dry-run` no crea `SportClient`: solo imprime `[DRY RUN] haria: ...`.
- Las acciones (levantarse, saludar) duran varios segundos y bloquean el
  programa. **La parada de emergencia es el mando: `L2` + `B`.**

## La cámara está baja

La cámara del Go2 va a 30 cm del suelo. A 2-3 m ve piernas y torso (GUIA 5.1),
pero para "mano por encima de la cabeza" tiene que ver la cabeza. Seguramente
haya que ponerse a 4 m o más.

Compruébalo en el paso 3. Si no es viable, en `configs/gestures.yaml` cambia
`referencia_mano: nariz` por `hombros`: entonces basta con levantar la muñeca
por encima de los hombros.

## Dependencia: MediaPipe

La imagen no trae `mediapipe`, ni el modelo de PoseLandmarker, que se descarga
aparte.

**En la imagen** (`docker/Dockerfile`, sección 6 "Entorno Python"), hay que:

1. Añadir `mediapipe` a la lista de `pip3 install`, **con la versión fijada**,
   igual que `mujoco==3.7.0`, para que cada reconstrucción no cambie la versión.
2. Añadir después una línea con el modelo:
   ```dockerfile
   RUN mkdir -p /opt/go2/models \
       && curl -fsSL -o /opt/go2/models/pose_landmarker_lite.task \
          https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
   ```
3. Reconstruir la imagen y comprobar dentro:
   ```bash
   pip check
   python3 -c "import cv2, mediapipe, numpy, ultralytics; print(cv2.__version__, mediapipe.__version__, numpy.__version__)"
   ```

Hay dos cosas que vigilar al reconstruir:
- `mediapipe` instala `opencv-contrib-python`, que choca con el `opencv-python`
  que ya hay (los dos traen `cv2`). Si falla `import cv2`, deja solo uno.
- Algunas versiones de `mediapipe` piden `numpy<2`, y la imagen permite numpy
  2.x (`"numpy>=1.26,<3"`). Elige una versión compatible o fija numpy.
  Comprueba también que uc04 (YOLO) sigue funcionando.

**Para salir del paso, sin reconstruir:**

```bash
pip install --user mediapipe
mkdir -p models && curl -fsSL -o models/pose_landmarker_lite.task \
    https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
```

Y en `configs/gestures.yaml`, `modelo_pose: models/pose_landmarker_lite.task`
(las rutas relativas se toman desde la raíz del repo).

## Estado

- [x] Detección de gestos con PoseLandmarker, persona más grande
- [x] Confirmación por fotogramas, cooldown y re-armado
- [x] Lectura de `rt/sportmodestate` y comprobación de estado antes de actuar
- [x] Dry-run con estado simulado para probar sin robot
- [ ] MediaPipe en la imagen Docker
- [ ] Valores de `mode` y `body_height` calibrados con `--estado`
- [ ] Probado con la cámara del robot (distancia a la que se ven los gestos)
- [ ] Probado en robot real
