# Guía: probar mis casos de uso en el Go2 real

> **Para quién:** quien ya ha seguido [`GUIA.md`](GUIA.md) hasta la parte 4 y
> quiere probar en el robot físico estos cuatro casos de uso:
>
> | caso | qué hace | necesita MediaPipe |
> |---|---|:-:|
> | [uc10_text_commands](#6-uc10--órdenes-escritas) | órdenes escritas en la terminal | ❌ |
> | [uc12_gesture_hello](#7-uc12--gestos-levantarse-saludar-tumbarse) | gestos: levantarse, saludar, tumbarse | ✅ |
> | [uc13_gesture_follow](#8-uc13--gestos--seguimiento--inclinación) | gestos + seguimiento a 2 m + inclinación | ✅ (salvo `--estado` y `--probar-inclinacion`) |
> | [uc04_person_following](#9-uc04--seguimiento-de-personas-follow_person) | seguimiento de personas con YOLO | ❌ |
>
> **Mi interfaz de red es `eno1`.** Todos los comandos de esta guía ya la llevan.

Etiquetas, igual que en la guía del tutor:

- 🖥️ **FUERA**: en tu ordenador (la línea empieza por `tunombre@tuordenador`)
- 📦 **DENTRO**: en la caja (la línea empieza por `go2@` o `root@`)

Orden de trabajo de cada sesión:

```text
1. Seguridad  →  2. Red  →  3. Entrar en la caja  →  4. Instalar MediaPipe (si hace falta)
              →  5. Caso de uso: --estado → --dry-run → real con --log → measure.py
```

---

## 1. 🛑 Seguridad (léelo siempre)

| regla | por qué |
|---|---|
| **Mando en la mano. `L2` + `B` para el robot al instante** | Es la única parada de emergencia de verdad. Practícalo 3 veces antes de empezar |
| **Espacio libre:** 2 m alrededor; **5 m y recorrido despejado** en uc13 y uc04 (siguen a la persona) | Los programas piden confirmar "3 m alrededor" antes de mover nada |
| **Batería por encima del 50 %** | Con poca batería Sport Mode se comporta peor |
| **App del móvil cerrada del todo** (no en segundo plano) | Pelea por el control y por la cámara (error `3205`, cámara sin imagen) |
| **Cable con holgura** | En uc13 y uc04 el robot anda: el cable no debe tensarse ni enredarse en las patas. Si hace falta, que alguien lo acompañe |
| **`Ctrl + C` una sola vez** | El primer `Ctrl + C` arranca el **cierre seguro** (enderezar, parar, tumbar). Pulsarlo otra vez puede cortarlo a medias. Espera a que imprima el final |

> Cortar el programa **no** detiene al robot por sí solo. Ante cualquier duda:
> **`L2` + `B`** primero, y luego el ordenador.

Antes de mover el robot, todos los programas piden escribir **`si`**. Y toda
prueba real va precedida de su versión `--dry-run`, que no mueve nada.

---

## 2. Conexión con el robot

Robot **tumbado**, encendido y **esperado un par de minutos** a que arranque.
Cable de red conectado entre el portátil y el robot. App del móvil cerrada.

### 2.1 Ver qué interfaz tiene cable

🖥️ FUERA, en la carpeta `go2-research-gestos`:

```bash
./env/go2_net.sh detect
```

**Qué tiene que salir:** en "Cuales tienen enlace fisico (carrier) ahora mismo":

```text
[ok] eno1 tiene enlace
```

Si sale `eno1 sin enlace (NO-CARRIER)`: cable mal puesto o robot apagado.

### 2.2 Crear el perfil de red (solo la primera vez)

🖥️ FUERA (pide la contraseña):

```bash
./env/go2_net.sh create eno1
```

**Qué tiene que salir:** `[ok] Perfil creado (autoconnect OFF: se activa a mano con '... up')`
y una tabla con `ipv4.addresses: 192.168.123.222/24` e `ipv4.never-default: yes`.

> El perfil se llama `go2-eth`. No te quita internet: no pone ruta por defecto.
> Solo hay que crearlo una vez; en las siguientes sesiones empieza en 2.3.

### 2.3 Activar la red

🖥️ FUERA:

```bash
./env/go2_net.sh up
```

**Qué tiene que salir:** una línea de `eno1` con `192.168.123.222/24` y `[ok] Activo`.

### 2.4 Comprobar que el robot responde

🖥️ FUERA:

```bash
./env/go2_net.sh probe
```

**Qué tiene que salir** (4 pasos):

```text
==> 1/4  Enlace fisico
[ok] eno1 tiene carrier
==> 2/4  Direccion IP del host
eno1   UP   192.168.123.222/24
==> 3/4  Ping al robot
[ok] 192.168.123.161 responde
[x] 192.168.123.18 no responde      <- normal si el robot no es EDU (es la Jetson)
==> 4/4  Trafico ARP (5 s) ...
```

Lo importante es **`192.168.123.161 responde`**. El paso 4 usa `sudo` y puede
pedir la contraseña.

| resultado de `probe` | significa |
|---|---|
| ping OK | sigue adelante |
| ARP sí / ping no | máscara o IP mal: revisa que sea 192.168.123.0/24 |
| ni ARP ni ping | cable, puerto, o el robot aún no ha arrancado del todo |

### 2.5 Puerta de comprobación

🖥️ FUERA:

```bash
python3 tools/doctor.py
```

**Qué tiene que salir:** en la sección `4. Red del robot`:

```text
[ok] GO2_IFACE=eno1 existe
[ok] IP del robot configurada   192.168.123.222/24
[ok] el ROBOT RESPONDE   192.168.123.161
```

y al final **`TODO CORRECTO`** (los avisos `[--]` no bloquean; los `[XX]` sí,
y cada uno trae su arreglo). `python3 tools/doctor.py --fix` aplica los
arreglos automatizables.

> 🛑 **No sigas hasta ver `TODO CORRECTO`.** Si `.env` no tiene
> `GO2_IFACE=eno1`, corrígelo con
> `sed -i "s/^GO2_IFACE=.*/GO2_IFACE=eno1/" .env`.

---

## 3. Entrar en la caja en modo robot

### 3.1 Arrancar la caja

🖥️ FUERA:

```bash
xhost +local:docker
docker compose -f docker-compose.yml -f docker-compose.linux.yml \
    --profile real run --rm real bash
```

- `xhost +local:docker` da permiso a la caja para abrir ventanas en tu pantalla.
- `--profile real` usa la red del portátil (`network_mode: host`), que es lo
  que necesita el DDS para hablar con el robot por `eno1`.
- `--rm` borra la caja al salir: **todo lo que instales dentro se pierde**
  (por eso la sección 4 guarda MediaPipe en `/workspace/.wheels`).

**Qué tiene que salir:** el resumen del arranque,

```text
  Entorno Go2 listo
    modo      : real
    domain    : 0
    interfaz  : eno1
    GUI       : none
```

y la línea pasa a empezar por `go2@` o `root@`: estás 📦 **DENTRO**.

### 3.2 Activar las ventanas: `export DISPLAY=:0 QT_X11_NO_MITSHM=1`

📦 DENTRO, **nada más entrar** y antes de cualquier comando con `--show`:

```bash
export DISPLAY=:0 QT_X11_NO_MITSHM=1
```

**Para qué sirve:**

| variable | qué hace |
|---|---|
| `DISPLAY=:0` | Le dice a OpenCV **en qué pantalla** abrir las ventanas. El perfil `real` arranca con `GO2_GUI: none`, y en ese caso el arranque de la caja pone `DISPLAY=:99`, una pantalla **virtual e invisible** (Xvfb). Sin este `export`, la ventana de `--show` se abre... en una pantalla que nadie ve, y no da ningún error. `:0` es la pantalla real de tu portátil, accesible porque la caja comparte la red del portátil y has hecho `xhost +local:docker` |
| `QT_X11_NO_MITSHM=1` | Desactiva la **memoria compartida de X11** (MIT-SHM). La caja y el portátil no comparten esa memoria, y sin esta variable la ventana de Qt/OpenCV puede salir gris o negra, o fallar con `BadAccess` |

Compruébalo:

```bash
echo $DISPLAY          # debe decir :0
```

> Si fuera de la caja `echo $DISPLAY` dice otra cosa (por ejemplo `:1`), usa
> ese valor dentro. Si entras por `ssh`, la pantalla del portátil sigue siendo
> `:0`.
>
> **Alternativa** (la que usan los README de uc12 y uc13): pasar las variables
> al arrancar la caja, sin `export` dentro:
>
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.linux.yml \
>     --profile real run --rm \
>     -e GO2_GUI=x11 -e DISPLAY=$DISPLAY -e QT_X11_NO_MITSHM=1 \
>     -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
>     real bash
> ```

### 3.3 Comprobaciones rápidas dentro (no mueven el robot)

📦 DENTRO:

```bash
python3 tools/doctor.py
```

Dentro de la caja comprueba otras cosas: `GO2_IFACE`, que `eno1` es visible,
que **el robot responde**, las bibliotecas, `cv2.imshow funciona` y
`camara del robot ... codigo 0`.

```bash
python3 tools/dds_smoketest.py --mode real --iface eno1 --duration 20
python3 tools/robot_camera.py --iface eno1 --seconds 20 --width 800
```

- `dds_smoketest`: escucha 20 s y muestra un informe sin errores.
- `robot_camera`: abre una ventana con lo que ve el robot (~24 fps). Si no se
  abre ventana, vuelve a 3.2.

---

## 4. Qué instalar dentro de la caja

La imagen ya trae `numpy`, OpenCV, `torch`, `ultralytics` (YOLO), el SDK de
Unitree, etc. **Lo único que falta es MediaPipe** y su modelo de pose.

### 4.1 Qué caso de uso lo necesita

| caso / modo | MediaPipe | modelo `pose_landmarker_lite.task` | YOLO (`yolo11n.pt`) |
|---|:-:|:-:|:-:|
| uc10 (todos los modos) | ❌ | ❌ | ❌ |
| uc12 `--estado` | ❌ | ❌ | ❌ |
| uc12 con cámara (`--dry-run`, real) | ✅ | ✅ | ❌ |
| uc13 `--estado` | ❌ | ❌ | ❌ |
| uc13 `--probar-inclinacion` | ❌ | ❌ | ❌ |
| uc13 con cámara (`--dry-run`, real) | ✅ | ✅ | ✅ (ya en la imagen y en la raíz del repo) |
| uc04 (todos los modos) | ❌ | ❌ | ✅ (ya en la imagen y en la raíz del repo) |

Las configuraciones de uc12 y uc13 buscan el modelo en
**`/tmp/go2models/pose_landmarker_lite.task`** (`modelo_pose:` en sus `.yaml`).

### 4.2 Por qué se instala de una forma rara

- `mediapipe` pide `opencv-contrib-python`, que **pisaría el OpenCV de la
  imagen** (el que dibuja las ventanas `--show`). Por eso se instala con
  `--no-deps`, y sus otras dependencias (`absl-py`, `sounddevice`, `certifi`)
  aparte.
- La caja se arranca con `--rm`: **al salir, MediaPipe desaparece**. Para no
  descargarlo cada vez, se guarda en **`/workspace/.wheels`**. `/workspace` es
  la carpeta del repo montada desde tu portátil, así que lo que pongas ahí
  sobrevive entre sesiones (en el portátil está en `go2-research-gestos/.wheels`).

> ⚠️ `.wheels/` no está en `.gitignore`: **no lo añadas a git** (`git add .`
> lo subiría). Usa `git add` con rutas concretas.

> ℹ️ Los pasos 4.3 y 4.4 (guardar e instalar desde `.wheels`) **no se han
> probado todavía**. Si algo falla, usa la instalación directa de 4.6, que es
> la de los README y sí está probada.

### 4.3 Guardar MediaPipe en `/workspace/.wheels` (una vez, con internet)

📦 DENTRO, con internet en el portátil (por ejemplo, Wi-Fi además del cable
del robot):

```bash
mkdir -p /workspace/.wheels
pip3 download --no-deps -d /workspace/.wheels mediapipe==1.2.3
pip3 download -d /workspace/.wheels "absl-py~=2.3" "sounddevice~=0.5" certifi
curl -fsSL -o /workspace/.wheels/pose_landmarker_lite.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
ls -lh /workspace/.wheels
```

**Qué tiene que salir:** varios ficheros `.whl` (`mediapipe-1.2.3-...whl`,
`absl_py-...`, `sounddevice-...`, `certifi-...` y alguno más que arrastran,
como `cffi`) y `pose_landmarker_lite.task` (unos 5 MB).

### 4.4 Instalar desde `/workspace/.wheels` (cada sesión, sin internet)

📦 DENTRO, cada vez que entres en la caja para uc12 o uc13:

```bash
pip3 install --no-index --find-links /workspace/.wheels --no-deps mediapipe==1.2.3
pip3 install --no-index --find-links /workspace/.wheels "absl-py~=2.3" "sounddevice~=0.5" certifi
mkdir -p /tmp/go2models
cp /workspace/.wheels/pose_landmarker_lite.task /tmp/go2models/
```

- `--no-index` impide que pip vaya a internet; `--find-links` le dice dónde
  están los ficheros.
- El `cp` deja el modelo donde lo buscan los `.yaml` de uc12 y uc13.

### 4.5 Comprobar las versiones

📦 DENTRO:

```bash
python3 -c "import cv2, mediapipe; print(cv2.__version__, mediapipe.__version__)"
ls -lh /tmp/go2models/pose_landmarker_lite.task
pip3 check
```

**Qué tiene que salir:**

```text
5.0.0 1.2.3
```

(OpenCV de la imagen intacto y MediaPipe 1.2.3) y el fichero del modelo.

> ✅ **`pip3 check` dirá que `mediapipe` requiere `opencv-contrib-python`.
> Es lo esperado. NO lo instales:** pisaría el OpenCV de la imagen y dejarían
> de funcionar las ventanas `--show`.

### 4.6 Instalación directa (sin `.wheels`, la de los README)

Si no tienes `.wheels` o algo falla, 📦 DENTRO y con internet:

```bash
pip3 install --no-cache-dir --no-deps mediapipe==1.2.3
pip3 install --no-cache-dir "absl-py~=2.3" "sounddevice~=0.5" certifi
mkdir -p /tmp/go2models
curl -fsSL -o /tmp/go2models/pose_landmarker_lite.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
```

---

## 5. Antes de cada caso de uso

- Las órdenes de esta guía se escriben 📦 **DENTRO**, en `/workspace`.
- **Tres opciones que hay que poner siempre** con el robot, porque sus valores
  por defecto **no** son los del robot:

  | opción | por defecto | con el robot |
  |---|---|---|
  | `--source` (uc12, uc13, uc04) | `webcam` (portátil) | `robot` |
  | `--mode` (uc04) | `sim` | `real` |
  | `--iface` | `GO2_IFACE` del `.env` | `eno1` (lo ponemos explícito) |

- `--duration` por defecto es 300 s en uc12, uc13 y uc04.
- Con `--log`, cada ejecución se guarda en
  `experiments/<caso>/<fecha>_<sha>_<tipo>/` con `metrics.csv` y
  `manifest.json`. `eval/measure.py` resume **la última** (o la que digas con
  `--run`).

---

## 6. uc10 — Órdenes escritas

### Qué hace

Escribes una orden en la terminal, pulsas Enter y el robot la ejecuta con
Sport Mode. **No usa cámara, ni voz, ni MediaPipe.** Solo acciones discretas:
nunca anda (`Move`). Antes de cada orden comprueba la postura real del robot
(por `body_height`) y rechaza las incompatibles.

| escribes | qué hace el robot |
|---|---|
| `levántate`, `arriba`, `l` | se pone de pie (`StandUp` + `BalanceStand`); si está sentado, `RiseSit` |
| `túmbate`, `abajo`, `t` | se tumba (`StopMove` + `StandDown`); si está sentado, antes `RiseSit` |
| `saluda`, `hola`, `h` | saluda (`Hello`); si no está de pie, antes se levanta |
| `siéntate`, `s` | se sienta (`Sit`), solo si está de pie |
| `estado` | dice si está de pie, sentado o tumbado (con `mode` y `body_height`) |
| `ayuda` | la lista de órdenes |
| `salir`, `q` | termina. De pie y estable **se queda de pie**; si no, cierre seguro (tumbado) |

Da igual mayúsculas o tildes. Lo que escribas mientras el robot actúa se descarta.

**Distancia:** no importa (no hay cámara). Basta con **2–3 m libres** alrededor del robot.

### Comandos en orden

**1. Sin robot** (estado simulado, empieza TUMBADO). 📦 DENTRO:

```bash
python3 usecases/uc10_text_commands/deploy/text_commands.py --iface eno1 --dry-run --log
```

Qué sale: `DRY RUN: no se crea SportClient ni se envia nada; estado simulado`,
`estado inicial: TUMBADO`, la ayuda y el prompt `[TUMBADO] orden> `. Cada orden
imprime `[DRY RUN] haria: SportClient.StandUp()` etc.

**2. Estado, solo lectura** (no mueve nada, 10 s). Con el mando pon el robot
tumbado, sentado y de pie:

```bash
python3 usecases/uc10_text_commands/deploy/text_commands.py --iface eno1 --estado
```

Qué sale: `Pon el robot de pie, SENTADO y tumbado con el mando.` y una línea
con `mode` y `body_height` que debe clasificar bien cada postura:

| postura | `body_height` medido | rango en `text_commands.yaml` |
|---|---|---|
| tumbado | 0.070 m | 0.00 – 0.12 |
| sentado | 0.228 m | 0.20 – 0.26 |
| de pie | 0.311 – 0.320 m | 0.28 – 0.36 |

`mode` sale siempre `0` en nuestro Go2: es normal.

**3. De verdad.** Mando en la mano:

```bash
python3 usecases/uc10_text_commands/deploy/text_commands.py --mode real --iface eno1 --log
```

Qué sale:

```text
uc10 — ordenes escritas
  modo    : real (domain 0, eno1)
  ROBOT FISICO. Confirma:
   ...
  Escribe 'si' para continuar: si
salida: ...
estado inicial: TUMBADO
[TUMBADO] orden> l
  -> SportClient.StandUp()
  -> SportClient.BalanceStand()
[DE_PIE] orden> h
  -> SportClient.Hello()
[DE_PIE] orden> salir
  de pie y estable: StopMove y se queda de pie (sin StandDown)

>>> EL ROBOT QUEDA: DE PIE   (llamadas del cierre: StopMove)
```

Al arrancar **no se levanta solo**: espera órdenes.

**4. Resumen:**

```bash
python3 usecases/uc10_text_commands/eval/measure.py
```

### Cómo termina (importante)

| al salir | queda |
|---|---|
| `salir`, `q`, Ctrl-D o **Ctrl-C en el prompt**, de pie y estable | **de pie** (solo `StopMove`) |
| **Ctrl-C durante una orden**, o error | tumbado (cierre seguro) |
| sentado o dudoso | `RiseSit` y tumbado |
| tumbado | nada |
| sin datos del robot | no envía nada: **usa el mando** |

Siempre imprime `>>> EL ROBOT QUEDA: ...`.

### Problemas frecuentes

| ves | qué hacer |
|---|---|
| `No llega rt/sportmodestate: sin estado no se mueve el robot.` | Red o app del móvil. Repite `./env/go2_net.sh probe` (FUERA) |
| `No entiendo "..."` | Escribe `ayuda` |
| dice que ya está en esa postura y no hace nada | Correcto: no repite acciones |
| `sentado_sin_confirmar` tras `s` | El sensor no confirma sentado: comprueba con `estado` antes de salir |
| la postura sale `DESCONOCIDO` | Altura fuera de los rangos (está cambiando de postura) o sin datos: espera y escribe `estado` |

---

## 7. uc12 — Gestos: levantarse, saludar, tumbarse

### Qué hace

El robot mira con su cámara, detecta tu esqueleto con **MediaPipe Pose** y
responde a tres gestos. Nunca anda (`Move`) ni se sienta (`Sit`).

| gesto | robot TUMBADO | robot DE PIE | sentado / dudoso / sin datos |
|---|---|---|---|
| ✋ **mano levantada** (una muñeca por encima de la cabeza) | se levanta (`StandUp` + `BalanceStand`) | nada, y lo dice | nada |
| 🙅 **brazos cruzados** delante del pecho | nada | se tumba (`StopMove` + `StandDown`) | nada |
| 🤸 **brazos en T** (en cruz, horizontales) | nada | saluda (`Hello`) | nada |

Reglas de los gestos:

- **Mantenlo ~1 s** (15 fotogramas seguidos: el contador sube a `15/15`).
- Después de cada acción hay **5 s de cooldown**.
- **Baja los brazos** antes de repetir el mismo gesto (re-armado: en la
  ventana sale `(baja los brazos)` y en `--debug` `armado=no`).
- Al arrancar **no se levanta solo**: espera el gesto.

**Distancia:** la cámara está a **30 cm del suelo**. Para que vea la mano por
encima de la cabeza, ponte a **unos 4 m o más**, de frente. La T y los brazos
cruzados se ven también algo más cerca.

En la ventana, el esqueleto va en el color del gesto: **verde** mano,
**naranja** cruzados, **magenta** T, gris ninguno.

### Necesita

MediaPipe y el modelo (sección 4), salvo `--estado`.

### Comandos en orden

**0. Sin robot, con la webcam del portátil** (estado simulado, empieza tumbado):

```bash
python3 usecases/uc12_gesture_hello/deploy/gesture_hello.py --iface eno1 --source webcam --dry-run --show
```

Mano → `haria: StandUp...`, T → `haria: Hello()`, cruzados → `haria: StandDown`.

**1. Estado, solo lectura.** Con el mando pon el robot tumbado, sentado y de pie:

```bash
python3 usecases/uc12_gesture_hello/deploy/gesture_hello.py --iface eno1 --estado
```

Debe decir `TUMBADO`, `SENTADO` y `DE_PIE`.

**2. Cámara del robot sin mover nada** (ponte a ~4 m):

```bash
python3 usecases/uc12_gesture_hello/deploy/gesture_hello.py \
    --mode real --iface eno1 --source robot --dry-run --show --debug
```

Qué sale: `cargando MediaPipe Pose...`, `detector listo`, `estado inicial: ...`
y se abre la ventana con:

```text
gesto: mano  12/15
robot: TUMBADO  h 0.070
accion: -
listo                      (o "cooldown: 3.2 s")
DRY RUN
```

Con `--debug`, una línea por imagen:
`[dbg] ... gesto=mano cont=12/15 fresca=si ... estado=TUMBADO ... armado=si ... t: sep ...`.
Comprueba que **mano, cruzados y T llegan a 15** y que imprime `haria: ...`.
Que aparezcan `repetidas` es normal.

**3. De verdad.** Robot tumbado, mando en la mano:

```bash
python3 usecases/uc12_gesture_hello/deploy/gesture_hello.py \
    --mode real --iface eno1 --source robot --show --duration 120 --log
```

Qué sale: `ROBOT FISICO. Confirma:` → escribe `si`. Luego, por cada gesto:

```text
[  12.3 s] gesto mano, robot TUMBADO -> ...
  resultado: ...
```

o, si no toca: `gesto t: no hago nada (..., robot TUMBADO)`.

Para parar: `q` sobre la ventana o **un** `Ctrl + C`. Al terminar, si está de
pie, se tumba (`StopMove` + `StandDown`); sentado, antes `RiseSit`.

**4. Resumen:**

```bash
python3 usecases/uc12_gesture_hello/eval/measure.py
```

### Problemas frecuentes

| ves | qué hacer |
|---|---|
| la mano no se detecta nunca | Aléjate (≥ 4 m) para que se vea la cabeza. Si aun así no, en `configs/gestures.yaml` cambia `referencia_mano: nariz` por `hombros` |
| el contador sube y vuelve a 0 | Imagen vieja o MediaPipe pierde un brazo. Mantente quieto, de frente, con buena luz |
| `armado=no` / `(baja los brazos)` | Baja los brazos un momento y repite |
| `gesto ...: no hago nada (cooldown ...)` | Espera a que la ventana diga `listo` (5 s) |
| `Hello` no se repite | Durante el saludo (4 s) no se corta ni con Ctrl-C: espera |
| `la camara ha dejado de dar imagen` | 5 s sin imagen: el programa termina y tumba al robot. Ver tabla final |

---

## 8. uc13 — Gestos + seguimiento + inclinación

### Qué hace

Responde a **cuatro gestos** y puede **seguirte a unos 2 m**. Te sigue con
**YOLO** (vale aunque solo vea tus piernas) y lee los gestos con
**MediaPipe**. En reposo (quieto de pie, o siguiendo pero parado más de 1 s)
**inclina el cuerpo 0.25 rad (~14°) con el morro hacia arriba** para verte los
brazos; antes de andar se endereza (0.7 s). **Nunca anda inclinado.**

| gesto | TUMBADO | DE PIE quieto | SIGUIENDO / PAUSADO |
|---|---|---|---|
| ✋ **una mano** levantada | se levanta (`StandUp` + `BalanceStand`) | nada | nada |
| 🙌 **dos manos** levantadas | nada | **empieza a seguir** | **para** |
| 🤸 **T** (brazos en cruz) | nada | saluda (`Hello`) | **para** |
| 🙅 **brazos cruzados** | nada | se tumba (`StopMove` + `StandDown`) | nada ("para primero") |

Sentado, dudoso o sin datos: ningún gesto hace nada.

**Seguimiento:**

| | |
|---|---|
| objetivo | 2 m |
| arranca / para | arranca si estás a más de **2.5 m**, para si estás a menos de **2.15 m** |
| velocidad | 0.25 – 0.4 m/s hacia delante |
| marcha atrás | si estás a menos de **1.4 m**: −0.15 m/s, máximo 3 s |
| pérdida | sin verte 0.5 s: se para (PAUSADO); 5 s: deja de seguir |

**Distancias para los gestos:**

| quieres | ponte a |
|---|---|
| levantarlo (una mano) con el robot tumbado | **~4 m** (tumbado no se inclina) |
| T y brazos cruzados (de pie, inclinado) | **~2 m** |
| dos manos (de pie, inclinado) | **~2.5 m** (a 2 m no entran en la imagen) |
| **parar** mientras sigue | **T** (a 2 m) o dos manos dando medio paso atrás |

**Cooldown:** el mismo gesto tiene que dejar de verse para volver a contar.
Un gesto **distinto** sí cuenta durante el cooldown: queda **pendiente** y se
ejecuta al acabar si lo sigues haciendo. **Parar** no espera al cooldown.

### Necesita

MediaPipe y el modelo (sección 4) para todo **menos** `--estado` y
`--probar-inclinacion`. YOLO ya está.

### Comandos en orden

**0. Sin robot, con la webcam** (estado simulado, empieza tumbado):

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --iface eno1 --source webcam --dry-run --show
```

**1. Estado, solo lectura** (también muestra el pitch):

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --iface eno1 --estado
```

Qué sale: `leyendo rt/sportmodestate (solo lectura)...` y una línea
`mode 0  body_height 0.320 m  pitch ...  edad  20 ms  ->  DE_PIE`.

**2. Cámara del robot sin mover nada** (calibración y gestos). Robot de pie y quieto:

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \
    --mode real --iface eno1 --source robot --dry-run --show --debug
```

Qué sale al principio:

```text
uc13 — gestos + seguimiento a 2 m
  modo    : real (domain 0, eno1)
  fuente  : robot
  objetivo: 2.0 m (tam 0.786); calibrado 1.3 m (tam 0.888); marcha atras < 1.4 m ...
  inclinacion: si, Euler(0, -1 * 0.25 rad, 0) cada 0.1 s en reposo
  DRY RUN: no se crea SportClient ni se envia nada
cargando YOLO y MediaPipe Pose...
detectores listos
```

Comprueba:

- que YOLO te ve andando (`yolo=si`) aunque MediaPipe no vea la cabeza;
- `ms_yolo`, `ms_mp` y los fps (si bajan de ~10, sube
  `mediapipe_cada_n_andando` a 2 en el `.yaml`);
- que en reposo diría `Euler(0, -0.250, 0)` ~1 s después del `BalanceStand`.

**Recalibrar la distancia** (solo si hace falta): ponte de frente y quieto
**10 s** a **2.0 m** (medido con cinta desde la cámara). Cada 2 s sale
`[cal] mediana yolo_tam=0.xxx`: copia ese valor en `seguimiento.tam_objetivo`.
Repite a **1.3 m** y cópialo en `seguimiento.tam_cerca` (tiene que ser mayor).

**3. Probar la inclinación** (`--probar-inclinacion`). Robot **de pie**, mando en la mano. No necesita MediaPipe:

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \
    --mode real --iface eno1 --probar-inclinacion --log
```

Qué hace: pide `si`; imprime un diagnóstico de solo lectura (versión del API
de sport, `MotionSwitcher.CheckMode`, `mode`, `body_height`); luego
`BalanceStand`, espera 1 s, `Euler(0, -0.25, 0)` cada 0.1 s durante 0.5 s +
`--mantener`, y `Euler(0, 0, 0)` 2 s. Imprime la IMU, el código y la duración
de cada llamada, un bloque `== resultado ==` y al final pregunta:

```text
¿El morro (la camara) ha SUBIDO, mirando hacia arriba? [s/n]:
```

- **ha subido** → `signo` correcto (hoy `-1`).
- **ha bajado** → cambia `inclinacion.signo` en `configs/gesture_follow.yaml` y repite.
- **no se ha movido** → guarda toda la salida. Ver "primera vez" abajo.

| opción de `--probar-inclinacion` | por defecto | qué hace |
|---|---|---|
| `--mantener S` | 3 | segundos inclinado por ciclo |
| `--espera-balance S` | `espera_balance_s` del yaml (1) | espera tras `BalanceStand` antes del primer Euler |
| `--ciclos N` | 1 | ciclos inclinar → enderezar sin nuevo `BalanceStand` |
| `--log` | — | guarda `serie.csv` y `manifest.json` en `experiments/uc13_gesture_follow/<fecha>_<sha>_probar_inclinacion/` |

Variante para estudiar el fallo de la "primera vez" (robot recién encendido o
10–15 min sin órdenes):

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \
    --mode real --iface eno1 --probar-inclinacion --mantener 15 --ciclos 2 --log
```

Si el ciclo 1 no se mueve y el 2 sí, sale `PATRON`.

| código de respuesta | significa |
|---|---|
| `0` en todos | el robot acepta Euler |
| `3203` | el robot no implementa Euler (firmware / servicio de movimiento) |
| `3104` | sin respuesta (red, o el servicio de sport no está activo) |
| `3205` | otro cliente (app, mando) tiene el control |

**4. De verdad.** Robot **tumbado**, mando en la mano, recorrido despejado, cable con holgura:

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \
    --mode real --iface eno1 --source robot --show --duration 180 --log
```

Pide `si` (y te recuerda que `tam` y el signo de la inclinación estén
comprobados). La ventana muestra la caja de YOLO, el esqueleto, el gesto y su
contador, el estado (`TUMBADO / DE_PIE / SIGUIENDO / PAUSADO`), el cuerpo
(`RECTO / INCLINANDO / INCLINADO / ENDEREZANDO`), `vx` y `wz` (y por qué son
0), la distancia y el `COOLDOWN` en grande (con `pendiente: ...` debajo).

Secuencia de prueba:

1. **Una mano** a ~4 m → se levanta y ~1 s después mira hacia arriba.
2. **T** a 2 m → saluda.
3. **Dos manos** a ~2.5 m → se endereza y empieza a seguir.
4. Aléjate despacio → anda hacia ti; **para** si estás quieto; más de 1 s parado → se inclina.
5. **T** → para.
6. **Brazos cruzados** → se tumba.

Al salir (`q`, un `Ctrl + C` o fin de tiempo): se endereza, para y se tumba.

**5. Resumen:**

```bash
python3 usecases/uc13_gesture_follow/eval/measure.py
```

Imprime y anota en el `manifest.json`, entre otras:

| métrica | qué mirar |
|---|---|
| `persona_pct` | % de fotogramas con persona |
| `acciones_lanzadas`, `gestos_confirmados` | qué gestos funcionaron |
| `huecos_imagen_mayores_05s`, `hueco_imagen_max_s` | salud de la cámara |
| `fotogramas_siguiendo`, `distancia_mediana_siguiendo_m` | debería rondar 2 m |
| `vx_max`, `vx_min`, `wz_max_abs` | velocidades reales enviadas |
| `andando_bajo_0_2_pct` | % andando por debajo de 0.2 m/s (debe ser bajo: Sport Mode va a tirones ahí) |
| `arranques_por_min`, `giros_por_min` | si es alto, el seguimiento va a trompicones |
| `fotogramas_watchdog` | veces que se paró por imagen o estado viejo |

### Problemas frecuentes

| ves | qué hacer |
|---|---|
| la primera inclinación no hace nada (códigos 0) | Fallo conocido, sin causa confirmada: la siguiente sí suele funcionar. Repite `--probar-inclinacion` |
| "dos manos" no se detecta a 2 m | Normal: no entra en la imagen. Da medio paso atrás (~2.5 m) o usa la T para parar |
| no te ve la cabeza pero sigue | Normal: sigue con YOLO; MediaPipe solo hace falta para los gestos |
| no retrocede al acercarte | La marcha atrás a 0.15 m/s puede que Sport Mode la ignore (sin comprobar). Sube `marcha_atras.vx` a 0.2 |
| `AVISO: la marcha atras salta a tam ...` | La calibración deja la marcha atrás pegada a la saturación: sube `marcha_atras.d_m` |
| se para y queda PAUSADO | Te ha perdido 0.5 s. Vuelve al encuadre; a los 5 s deja de seguir |

---

## 9. uc04 — Seguimiento de personas (`follow_person`)

### Qué hace

Detecta personas con **YOLO** en la cámara del robot y le manda velocidades a
Sport Mode: **gira** para tenerte centrado y **avanza o retrocede** para
mantener tu tamaño en la imagen (≈ distancia). No hay gestos ni MediaPipe. Si
te pierde, **se para**.

| tú | el robot |
|---|---|
| te quedas quieto y centrado | quieto (`vx` y `wz` a 0) |
| das un paso lateral | gira hacia ti |
| te alejas | avanza |
| te acercas | retrocede |
| sales del encuadre | se para |

**Distancia:** ponte a **2–3 m**, de pie. El objetivo con la cámara del robot
es `tam 0.75` (≈ 2 m; medido: 1 m → 0.85, 3 m → 0.65). Solo te verá de
cintura para abajo y algo de torso.

> ⚠️ **Pon siempre `--mode real` y `--source robot`.** Por defecto es
> `--mode sim` y `--source webcam`: con eso no mueve el robot y no avisa.

### Comandos en orden

uc04 no tiene `--estado` ni `--debug`.

**1. Prueba sin mover el robot (obligatoria):**

```bash
python3 usecases/uc04_person_following/deploy/follow_person.py \
    --mode real --iface eno1 --source robot --show --dry-run
```

Qué sale:

```text
uc04 — seguimiento de personas
  modo    : real (domain 0, eno1)
  DRY RUN: no se envia ninguna velocidad
  objetivo de tamano (camara del robot): 0.75
cargando YOLO...
detector listo

en marcha. Ctrl-C para parar.
```

y la ventana `uc04 - seguimiento` con un recuadro sobre la persona y `vx`, `wz`.

> 🛑 **Comprobación crítica** (GUIA 5.2): **quieto y centrado, `vx` y `wz`
> tienen que ser 0** (o saltar poquísimo). Si no, **para** (`q` o `Ctrl + C`)
> y no sigas. Prueba también: sal del encuadre (→ 0), paso lateral (→ giro),
> aléjate (→ avance).

**2. De verdad.** Mando en la mano, 5 m libres, cable con holgura:

```bash
python3 usecases/uc04_person_following/deploy/follow_person.py \
    --mode real --iface eno1 --source robot --show --duration 60 --log
```

Qué sale: `ROBOT FISICO. Confirma:` → `si`; `cargando YOLO...`;
`levantando el robot (StandUp + BalanceStand)...` (unos 5 s, aún no anda); y
empieza a seguir. Al final:

```text
parando...
duracion 60.0 s | 1440 fotogramas | 24.0 fps
persona visible el 92 % del tiempo
```

Sin `--show`, imprime cada 15 fotogramas una línea así:

```text
persona  lat +0.02ZM  tam 0.74ZM(obj 0.75)  ->  vx +0.00  wz +0.00   24.0 fps
```

(`ZM` = dentro de la zona muerta: no corrige).

**3. Resultado:** uc04 **no tiene `eval/measure.py`**. Con `--log` queda en
`experiments/uc04_person_following/<fecha>_<sha>_follow/` (`metrics.csv` con
`t, visible, lateral, tamano, confianza, vx, wz`, y `manifest.json` con
`fps_medio` y `visible_pct`).

### Problemas frecuentes

| ves | qué hacer |
|---|---|
| no pide `si` y el robot no se mueve | Te has dejado `--mode real` (va a simulación) |
| la ventana muestra la webcam del portátil | Te has dejado `--source robot` |
| va a tirones o se queda quieto | Sport Mode ignora órdenes < 0.2 m/s. Camina despacio y con pasos claros |
| oscila a izquierda y derecha | Ruido de la detección: ponte de frente, quieto, con buena luz |
| `la camara ha dejado de dar imagen` y termina | Ver tabla final |

---

## 10. Referencia rápida

| qué | dónde | comando |
|---|:-:|---|
| ver interfaz con cable | 🖥️ | `./env/go2_net.sh detect` |
| crear perfil de red (1ª vez) | 🖥️ | `./env/go2_net.sh create eno1` |
| activar red | 🖥️ | `./env/go2_net.sh up` |
| comprobar robot | 🖥️ | `./env/go2_net.sh probe` |
| diagnóstico | 🖥️ / 📦 | `python3 tools/doctor.py` |
| permiso de ventanas | 🖥️ | `xhost +local:docker` |
| entrar en modo robot | 🖥️ | `docker compose -f docker-compose.yml -f docker-compose.linux.yml --profile real run --rm real bash` |
| ventanas en la pantalla real | 📦 | `export DISPLAY=:0 QT_X11_NO_MITSHM=1` |
| leer sensores | 📦 | `python3 tools/dds_smoketest.py --mode real --iface eno1 --duration 20` |
| ver la cámara | 📦 | `python3 tools/robot_camera.py --iface eno1 --seconds 20 --width 800` |
| guardar MediaPipe (1ª vez) | 📦 | ver 4.3 |
| instalar MediaPipe (cada sesión) | 📦 | ver 4.4 |
| comprobar versiones | 📦 | `python3 -c "import cv2, mediapipe; print(cv2.__version__, mediapipe.__version__)"` |
| **uc10** sin robot | 📦 | `python3 usecases/uc10_text_commands/deploy/text_commands.py --iface eno1 --dry-run --log` |
| **uc10** estado | 📦 | `python3 usecases/uc10_text_commands/deploy/text_commands.py --iface eno1 --estado` |
| **uc10** real | 📦 | `python3 usecases/uc10_text_commands/deploy/text_commands.py --mode real --iface eno1 --log` |
| **uc10** resumen | 📦 | `python3 usecases/uc10_text_commands/eval/measure.py` |
| **uc12** webcam | 📦 | `python3 usecases/uc12_gesture_hello/deploy/gesture_hello.py --iface eno1 --source webcam --dry-run --show` |
| **uc12** estado | 📦 | `python3 usecases/uc12_gesture_hello/deploy/gesture_hello.py --iface eno1 --estado` |
| **uc12** dry-run robot | 📦 | `python3 usecases/uc12_gesture_hello/deploy/gesture_hello.py --mode real --iface eno1 --source robot --dry-run --show --debug` |
| **uc12** real | 📦 | `python3 usecases/uc12_gesture_hello/deploy/gesture_hello.py --mode real --iface eno1 --source robot --show --duration 120 --log` |
| **uc12** resumen | 📦 | `python3 usecases/uc12_gesture_hello/eval/measure.py` |
| **uc13** webcam | 📦 | `python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --iface eno1 --source webcam --dry-run --show` |
| **uc13** estado | 📦 | `python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --iface eno1 --estado` |
| **uc13** dry-run robot | 📦 | `python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --mode real --iface eno1 --source robot --dry-run --show --debug` |
| **uc13** inclinación | 📦 | `python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --mode real --iface eno1 --probar-inclinacion --log` |
| **uc13** real | 📦 | `python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py --mode real --iface eno1 --source robot --show --duration 180 --log` |
| **uc13** resumen | 📦 | `python3 usecases/uc13_gesture_follow/eval/measure.py` |
| **uc04** dry-run | 📦 | `python3 usecases/uc04_person_following/deploy/follow_person.py --mode real --iface eno1 --source robot --show --dry-run` |
| **uc04** real | 📦 | `python3 usecases/uc04_person_following/deploy/follow_person.py --mode real --iface eno1 --source robot --show --duration 60 --log` |
| salir de la caja | 📦 | `exit` |
| quitar permiso de ventanas | 🖥️ | `xhost -local:docker` |
| quitar la red del robot | 🖥️ | `./env/go2_net.sh down` |

Opciones comunes (sacadas de `argparse`):

| opción | uc10 | uc12 | uc13 | uc04 |
|---|:-:|:-:|:-:|:-:|
| `--mode` | `real` | `real` | `real` | `sim` / `real` (def. `sim`) |
| `--iface`, `--config`, `--dry-run`, `--log` | ✅ | ✅ | ✅ | ✅ |
| `--estado` | ✅ | ✅ | ✅ | ❌ |
| `--source webcam\|robot` (def. `webcam`), `--camera`, `--show`, `--duration` (def. 300) | ❌ | ✅ | ✅ | ✅ |
| `--debug` | ❌ | ✅ | ✅ | ❌ |
| `--probar-inclinacion`, `--mantener`, `--espera-balance`, `--ciclos` | ❌ | ❌ | ✅ | ❌ |
| `eval/measure.py [--run CARPETA]` | ✅ | ✅ | ✅ | ❌ |

---

## 11. Problemas y soluciones

### Emergencias

| situación | qué hacer |
|---|---|
| **cualquier cosa rara** | **`L2` + `B`. Siempre lo primero** |
| anda sin control / no para | `L2` + `B`; después **un** `Ctrl + C` |
| el programa se cuelga | `L2` + `B`; luego cierra la terminal |
| imprime `USA EL MANDO: L2+B` | Hazlo: el cierre no ha podido tumbar al robot con seguridad |

### Errores frecuentes

| lo que ves | qué pasa | qué hacer |
|---|---|---|
| **`la camara ha dejado de dar imagen`** | La cámara del robot no entrega imagen (uc12/uc13: 5 s sin imagen; uc04: al primer fallo). El programa termina y hace el cierre | 1) App del móvil **cerrada del todo**. 2) Cable bien y con holgura (un tirón lo afloja). 3) `./env/go2_net.sh probe` (FUERA). 4) Prueba `python3 tools/robot_camera.py --iface eno1 --seconds 20`. Si sigue, reinicia el robot (tumbado) |
| **la ventana de `--show` no aparece** (sin error) | `DISPLAY=:99`: dibuja en la pantalla virtual invisible | 📦 `export DISPLAY=:0 QT_X11_NO_MITSHM=1` (3.2) y repite. Comprueba `echo $DISPLAY` FUERA |
| `cannot connect to X server` / `Can't open display` / `Authorization required` | Falta el permiso de ventanas | 🖥️ `xhost +local:docker` y repite (no hace falta salir de la caja) |
| ventana gris, negra o `BadAccess` | MIT-SHM | 📦 `export QT_X11_NO_MITSHM=1` |
| **`armado=no`** / `(baja los brazos)` | Tras una acción, el mismo gesto no cuenta hasta que lo deshagas | Baja los brazos un momento |
| **`cooldown`** / `no hago nada (cooldown ...)` | 5 s tras cada acción | Espera a `listo`. En uc13 otro gesto queda `pendiente` |
| `no hago nada (..., robot SENTADO / DUDOSO / SIN_DATOS)` | Postura no compatible o altura de paso | Espera a que se asiente; si está sentado, ponlo de pie o tumbado con el mando |
| el contador no llega a 15 | Gesto poco claro, demasiado cerca o imagen vieja (`fresca=NO`) | Aléjate (uc12: ~4 m), de frente, quieto, buena luz |
| **`ModuleNotFoundError: No module named 'mediapipe'`** | La caja es `--rm`: al salir se perdió | 📦 Repite 4.4 (desde `.wheels`) o 4.6 |
| `ModuleNotFoundError: No module named 'numpy'` (o `cv2`, `unitree_sdk2py`) | Estás **FUERA** de la caja | Entra en la caja (3.1) |
| no encuentra `/tmp/go2models/pose_landmarker_lite.task` | `/tmp` también se borra al salir | 📦 `mkdir -p /tmp/go2models && cp /workspace/.wheels/pose_landmarker_lite.task /tmp/go2models/` |
| **`pip check`: `mediapipe ... requires opencv-contrib-python`** | **Esperado** | **No lo instales**: rompería OpenCV y las ventanas |
| `pip3 install --no-index ...`: `No matching distribution found` | Falta ese fichero en `.wheels` | Con internet, repite 4.3, o usa 4.6 |
| `No llega rt/sportmodestate` | No llegan datos del robot | Red (`probe`), app cerrada, `--iface eno1` |
| `does not match an available interface` | Nombre de interfaz incorrecto | Usa `eno1`; revisa `GO2_IFACE=eno1` en `.env` |
| `ERROR: GO2_MODE=real requiere GO2_IFACE` al entrar | `.env` sin `GO2_IFACE` | 🖥️ `sed -i "s/^GO2_IFACE=.*/GO2_IFACE=eno1/" .env` |
| código `3205` | Otro cliente controla el robot | Cierra la app del móvil; suelta el mando |
| código `3104` | Sin respuesta del servicio de sport | Red; espera a que el robot termine de arrancar |
| la inclinación (uc13) no hace nada la primera vez | Fallo conocido, sin causa confirmada | Repite `--probar-inclinacion`; guarda la salida con `--log` |
| el robot va a tirones o no anda | Sport Mode ignora < 0.2 m/s | Normal. No es un fallo |
| el robot se dobla o se tumba al empezar/terminar | Toma y suelta el control | Normal: el cierre lo deja en postura segura (uc10 puede dejarlo de pie) |
| uc04 no pide `si` y no mueve nada | Falta `--mode real` | Añádelo |
| la ventana enseña la webcam del portátil | Falta `--source robot` | Añádelo |

### Al terminar

1. 📦 `exit` para salir de la caja.
2. Apaga el robot cuando esté tumbado.
3. 🖥️ `xhost -local:docker`
4. 🖥️ Opcional: `./env/go2_net.sh down`

---

Más detalle de cada caso en su README:
[uc10](../usecases/uc10_text_commands/README.md) ·
[uc12](../usecases/uc12_gesture_hello/README.md) ·
[uc13](../usecases/uc13_gesture_follow/README.md) ·
[uc04](../usecases/uc04_person_following/README.md).
Seguridad completa: [`SAFETY.md`](SAFETY.md). Guía del tutor: [`GUIA.md`](GUIA.md).
