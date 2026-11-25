# Guía rápida: de un Ubuntu recién instalado a uc13 y uc10 en el Go2

Para alguien que **nunca ha usado** el robot ni este proyecto. Necesitas:

- un PC con **Ubuntu** (escritorio, recién instalado vale) e internet;
- el **Unitree Go2** con su batería cargada y su **mando**;
- un **cable Ethernet** (si el PC no tiene puerto de red, un adaptador USB‑Ethernet).

Cada paso dice **qué escribir** y **cómo saber que ha salido bien** (✅). Si
algo no sale como dice ✅, ve a [Si falla...](#si-falla).

Leyenda:

- 🖥️ **FUERA** = una terminal normal del PC (`Ctrl + Alt + T`).
- 📦 **DENTRO** = dentro del contenedor (paso 6 en adelante). Se nota porque el
  principio de la línea cambia a `root@...` o `go2@...`.
- `<tu-interfaz>` = el nombre de tu puerto de red, que averiguas en el paso 5.
  **Escribe el tuyo** (por ejemplo `enp3s0`), sin los `< >`.

> La parte de red sigue la guía completa del proyecto,
> [`GUIA_MIS_CASOS_USO.md`, sección 2](GUIA_MIS_CASOS_USO.md#2-conexión-con-el-robot),
> y su script `env/go2_net.sh`, pero sin suponer que tu puerto se llama `eno1`.

---

## 🛑 Seguridad (léelo antes de tocar el robot)

| regla | por qué |
|---|---|
| **Mando en la mano. `L2` + `B` = parada inmediata** | Es la única parada de emergencia de verdad. Practícalo antes de empezar |
| **Espacio libre**: 2–3 m alrededor (uc10) y **5 m despejados** (uc13, el robot anda hacia ti) | El robot se mueve de verdad |
| **Batería por encima del 50 %** | Con poca batería el robot se comporta peor |
| **App del móvil de Unitree cerrada del todo** (no en segundo plano) | Le quita el control al PC (error `3205`) y deja la cámara sin imagen |
| **Cable con holgura** | En uc13 el robot anda: que el cable no se tense ni se enrede en las patas |
| **`Ctrl + C` una sola vez** | El primero arranca el *cierre seguro*; un segundo lo corta a medias |

Cortar el programa **no** detiene al robot por sí solo. Ante cualquier duda:
primero **`L2` + `B`**, después el ordenador.

---

## 1. Instalar git y Docker (una vez)

🖥️ FUERA:

```bash
sudo apt update
sudo apt install -y git curl docker.io docker-compose-v2 x11-xserver-utils
```

(Pide tu contraseña; al escribirla no se ve nada, es normal.)

- `docker.io` y `docker-compose-v2` son Docker y `docker compose`.
- `x11-xserver-utils` trae `xhost`, que deja al contenedor abrir ventanas.

> Si `apt` dice `Unable to locate package docker-compose-v2` (Ubuntu antiguo),
> instala Docker con la guía oficial: <https://docs.docker.com/engine/install/ubuntu/>
> y vuelve aquí.

✅ Comprueba:

```bash
git --version
docker --version
docker compose version
```

Deben salir tres números de versión, sin errores.

### 1.1 Usar Docker sin `sudo`

🖥️ FUERA:

```bash
sudo usermod -aG docker $USER
```

Ahora **cierra la sesión de Ubuntu y vuelve a entrar** (o reinicia el PC). Es
obligatorio: el permiso nuevo solo se aplica al entrar.

✅ Comprueba (sin `sudo`):

```bash
docker run --rm hello-world
```

Debe salir `Hello from Docker!`. Si sale `permission denied`, no has cerrado
sesión: ver [Si falla...](#permission-denied-con-docker).

### 1.2 ¿Y la tarjeta gráfica NVIDIA?

**No hace falta nada.** La imagen de este proyecto funciona **solo con CPU**
(es una decisión del proyecto, para que vaya en cualquier PC): YOLO,
MediaPipe y el control corren en el procesador. Por tanto:

- **No** instales `nvidia-container-toolkit` ni configures GPU para Docker.
- **Tengas NVIDIA, Intel, AMD o ninguna gráfica**, los pasos son los mismos.
- Los drivers que Ubuntu instala por defecto bastan para ver las ventanas.

---

## 2. Descargar el proyecto (una vez)

🖥️ FUERA:

```bash
cd ~
git clone https://github.com/bingenperez/go2-research-gestos.git
cd go2-research-gestos
```

✅ `ls` muestra, entre otros, `run.sh`, `docker-compose.yml`, `usecases` y `docs`.

Descarga ahora el peso de YOLO, que git no guarda (uc13 lo necesita para verte):

```bash
./tools/get_models.sh
```

✅ Sale `[ok] yolo11n.pt descargado y comprobado` (o `ya esta`). Si se te
olvida, `./run.sh` lo descarga solo la primera vez (con internet).

> **Todos los comandos 🖥️ FUERA de aquí en adelante se escriben dentro de
> esta carpeta.** Si abres otra terminal, primero: `cd ~/go2-research-gestos`

---

## 3. Descargar la imagen de Docker (una vez, ~2 GB comprimida)

🖥️ FUERA:

```bash
docker pull ghcr.io/bingenperez/go2-research-gestos:latest
```

Es pública: no hace falta iniciar sesión. Tarda varios minutos.

✅ Termina con `Status: Downloaded newer image for ghcr.io/bingenperez/go2-research-gestos:latest`
(o `Image is up to date`). Y:

```bash
docker images ghcr.io/bingenperez/go2-research-gestos
```

muestra una línea con `latest` (unos 6,6 GB).

La imagen ya lo trae todo: ROS 2, el SDK de Unitree, OpenCV, YOLO, **MediaPipe
y su modelo de pose**. No hay que instalar nada más.

---

## 4. Preparar el fichero de configuración `.env` (una vez)

🖥️ FUERA:

```bash
cp -n .env.example .env
```

✅ `ls -a` muestra `.env`. Lo rellenarás en el paso 5.5 (tu interfaz). `.env`
**no se sube nunca a git** (está en `.gitignore`).

---

## 5. Conectar el perro

### 5.1 Encender el robot

1. Pon el robot **tumbado** en el suelo, con espacio libre alrededor.
2. Enciende la batería: **una pulsación corta y, justo después, una larga
   (unos 2 s)** en el botón de la batería, hasta que se enciendan sus luces.
3. **Espera 1–2 minutos** a que arranque del todo. Puede levantarse solo al
   terminar de arrancar; es normal.
4. Enciende el **mando**.
5. Cierra **del todo** la app de Unitree en el móvil.

### 5.2 Averiguar el nombre de TU puerto de red

`eno1` **no es algo que se crea**: es el nombre que Linux da al puerto de red,
y **cambia en cada PC** (`eno1`, `enp3s0`, `eth0`, `enx00e04c...` si es un
adaptador USB...). Para saber el tuyo:

**a)** Con el cable **desenchufado**, 🖥️ FUERA:

```bash
ip -br a
```

**b)** Enchufa el cable entre el PC y el robot y repite:

```bash
ip -br a
```

**c)** Tu interfaz es la que ha pasado de `DOWN` a `UP` (o la que acaba de
aparecer, si es un adaptador USB). Ejemplo:

```text
antes:   enp3s0   DOWN
después: enp3s0   UP     fe80::1a2b:.../64
```

Aquí `<tu-interfaz>` = `enp3s0`. **Ignora** `lo`, `wl...` (Wi‑Fi), `docker0`,
`br-...` y `veth...`.

✅ Atajo para confirmarlo (dice qué puertos tienen el cable conectado ahora):

```bash
./env/go2_net.sh detect
```

Debe salir `[ok] <tu-interfaz> tiene enlace`.

### 5.3 Crear la conexión con IP fija (solo la primera vez)

El robot vive en la red `192.168.123.x` y su IP es **`192.168.123.161`**.
Tu PC tiene que estar en esa red con una IP fija: **`192.168.123.222`**.

🖥️ FUERA:

```bash
./env/go2_net.sh create <tu-interfaz>
```

Es lo mismo que escribir a mano:

```bash
nmcli con add type ethernet ifname <tu-interfaz> con-name go2-eth \
    ipv4.method manual ipv4.addresses 192.168.123.222/24 \
    ipv4.never-default yes ipv6.method disabled connection.autoconnect no
```

- **No te quita internet**: `never-default` deja el Wi‑Fi como salida a internet.
- `autoconnect no`: la conexión solo se activa cuando tú lo pides (5.4).

✅ Sale `[ok] Perfil creado` y una tabla con `ipv4.addresses: 192.168.123.222/24`.

### 5.4 Activar la conexión y hacer ping al robot (cada vez)

🖥️ FUERA:

```bash
./env/go2_net.sh up
ping -c 3 192.168.123.161
```

✅ `./env/go2_net.sh up` termina con una línea de `<tu-interfaz>` con
`192.168.123.222/24`, y el ping con **`3 received, 0% packet loss`**.

Diagnóstico completo, si quieres (enlace, IP, ping, ARP; puede pedir la contraseña):

```bash
./env/go2_net.sh probe
```

✅ Lo importante es `[ok] 192.168.123.161 responde`. (`192.168.123.18 no responde`
es normal si tu Go2 no es la versión EDU.)

### 5.5 Guardar tu interfaz en `.env` (una vez)

🖥️ FUERA:

```bash
sed -i "s/^GO2_IFACE=.*/GO2_IFACE=<tu-interfaz>/" .env
grep GO2_IFACE .env
```

✅ Sale `GO2_IFACE=<tu-interfaz>` con tu nombre real.

---

## 6. Entrar en el contenedor (con ventanas)

🖥️ FUERA:

```bash
./run.sh <tu-interfaz>
```

(Con `GO2_IFACE` ya en `.env`, vale `./run.sh` a secas.)

`run.sh` hace por ti tres cosas: da permiso para abrir ventanas en tu pantalla
(`xhost +local:docker`, que retira al salir), usa la red del PC (para hablar
con el robot) y abre el contenedor con la imagen del paso 3.

✅ Sale:

```text
interfaz del robot: <tu-interfaz>   pantalla: :0
...
  Entorno Go2 listo
    modo      : real
    domain    : 0
    interfaz  : <tu-interfaz>
    GUI       : x11
```

y la línea cambia a `root@...`: estás 📦 **DENTRO**.

✅ Comprobación rápida 📦 DENTRO (no mueve nada):

```bash
python3 -c "import cv2, mediapipe; print(cv2.__version__, mediapipe.__version__)"
python3 tools/dds_smoketest.py --mode real --iface <tu-interfaz> --duration 10
python3 tools/robot_camera.py --iface <tu-interfaz> --seconds 10 --width 800
```

- La primera imprime `5.0.0 1.2.3`.
- La segunda escucha al robot 10 s y muestra un informe **sin errores**.
- La tercera **abre una ventana con lo que ve el robot**. Si no se abre, ver
  [No se abre la ventana](#no-se-abre-la-ventana).

Para salir del contenedor: `exit`. Se borra al salir, pero tu carpeta del
proyecto y lo que guarden los programas (`experiments/`) se quedan.

---

## 7. uc13 — gestos + seguimiento

El robot obedece **gestos** que ve con su cámara y puede **seguirte** a unos 2 m.

| gesto | robot tumbado | robot de pie quieto | robot siguiéndote |
|---|---|---|---|
| **una mano** levantada (a ~4 m) | se levanta | — | — |
| **saludar** con la mano (vaivén) | se levanta | saluda | — |
| **T** (brazos en cruz) | — | saluda | **para** |
| **dos manos** levantadas (a ~2.5 m) | — | **empieza a seguirte** | **para** |
| **brazos cruzados** | — | se tumba | — ("para primero") |

Cómo hacer los gestos: de frente al robot, **quieto**, con buena luz, y
mantenerlo hasta que el contador de la ventana llegue al final (~1 s). Tras
cada acción hay 5 s de pausa (`COOLDOWN`) y hay que **bajar los brazos** antes
de repetir el mismo gesto.

### 7.1 Prueba en seco (`--dry-run`: NO mueve el robot)

Robot **de pie y quieto** (si está tumbado, también vale: solo mira).
📦 DENTRO:

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \
    --mode real --iface <tu-interfaz> --source robot --dry-run --show --debug
```

✅ En la terminal:

```text
uc13 — gestos + seguimiento a 2 m
  modo    : real (domain 0, <tu-interfaz>)
  fuente  : robot
  DRY RUN: no se crea SportClient ni se envia nada
cargando YOLO y MediaPipe Pose...
detectores listos
```

✅ Se abre una ventana con la imagen del robot, una caja alrededor de ti, tu
esqueleto y el gesto que detecta. Haz los gestos de la tabla: en la terminal
sale `haria: SportClient....` en vez de moverse.

Para salir: **`q`** con la ventana seleccionada, o **un** `Ctrl + C`.

### 7.2 Prueba real (el robot SE MUEVE)

Robot **tumbado** (si está de pie, túmbalo con uc10: paso 8.2, orden `t`),
mando en la mano, **5 m despejados**, cable con holgura. 📦 DENTRO:

```bash
python3 usecases/uc13_gesture_follow/deploy/gesture_follow.py \
    --mode real --iface <tu-interfaz> --source robot --show --duration 180 --log
```

✅ Te pide confirmar: escribe **`si`** y Enter. Se abre la ventana. Secuencia:

1. **Una mano** levantada a ~4 m → se levanta y, ~1 s después, inclina el
   morro hacia arriba (para verte mejor).
2. **T** a ~2 m → saluda.
3. **Dos manos** a ~2.5 m → empieza a seguirte.
4. Aléjate despacio → anda hacia ti; si te quedas quieto, para.
5. **T** → deja de seguirte.
6. **Brazos cruzados** → se tumba.

`--duration 180`: termina solo a los 3 minutos. `--log` guarda los datos en
`experiments/uc13_gesture_follow/`.

### 7.3 Cómo parar el robot (uc13)

1. **`L2` + `B` en el mando**: parada inmediata. **Siempre lo primero** si
   algo va raro.
2. En el programa: **`q`** en la ventana o **UN SOLO `Ctrl + C`**. Arranca el
   *cierre seguro*: se endereza, para y se tumba. **Espera** a que termine de
   imprimir.
3. Si imprime `USA EL MANDO: L2+B`, hazlo.

---

## 8. uc10 — órdenes escritas

Escribes una orden en la terminal, pulsas Enter y el robot la hace. **No usa
cámara ni internet.** Nunca anda: solo cambia de postura y saluda.

| escribe | qué hace |
|---|---|
| `levántate`, `arriba` o `l` | se pone de pie |
| `túmbate`, `abajo` o `t` | se tumba |
| `saluda`, `hola` o `h` | saluda (si no está de pie, antes se levanta) |
| `siéntate` o `s` | se sienta (solo si está de pie) |
| `estado` | dice si está de pie, sentado o tumbado |
| `ayuda` | lista de órdenes |
| `salir` o `q` | termina (ver 8.3) |

Da igual mayúsculas o tildes.

> **¿Y la clave de Gemini?** **uc10 no la usa**: entiende un vocabulario fijo,
> sin inteligencia artificial ni internet. La clave de Gemini solo la usa otro
> caso de uso, `uc06_lenguaje_natural`, que no está en esta guía. Si algún día
> la necesitas:
>
> 1. Entra en <https://aistudio.google.com/apikey> con una cuenta de Google y
>    pulsa **Create API key**. Copia la clave.
> 2. 🖥️ FUERA, abre `.env` con un editor (`nano .env`) y pégala en la línea
>    `GEMINI_API_KEY=`, **sin comillas ni espacios**:
>    ```text
>    GEMINI_API_KEY=<pega-aquí-tu-clave>
>    ```
>    Guarda (`Ctrl + O`, Enter) y sal (`Ctrl + X`).
> 3. La clave es **personal y secreta**: no la pegues en chats, issues ni
>    commits. `.env` ya está en `.gitignore`; nunca hagas `git add -f .env`.

### 8.1 Prueba en seco (`--dry-run`: NO mueve el robot)

No necesita ni el robot: el estado es simulado y empieza TUMBADO. 📦 DENTRO:

```bash
python3 usecases/uc10_text_commands/deploy/text_commands.py --iface <tu-interfaz> --dry-run
```

✅ Sale `DRY RUN: no se crea SportClient ni se envia nada; estado simulado`,
`estado inicial: TUMBADO`, la ayuda y el prompt `[TUMBADO] orden> `.
Escribe `l` → `[DRY RUN] haria: SportClient.StandUp()`. Escribe `q` para salir.

Con el robot conectado, comprueba también que **lee bien su postura** (solo
lectura, 10 s; no mueve nada):

```bash
python3 usecases/uc10_text_commands/deploy/text_commands.py --iface <tu-interfaz> --estado
```

✅ Una línea por segundo con `body_height` y la postura (`TUMBADO`, `DE_PIE`
o `SENTADO`) que coincide con la real.

### 8.2 Prueba real (el robot SE MUEVE)

Mando en la mano, 2–3 m libres. 📦 DENTRO:

```bash
python3 usecases/uc10_text_commands/deploy/text_commands.py --mode real --iface <tu-interfaz> --log
```

✅ Pide confirmar: escribe **`si`**. Sale `estado inicial: TUMBADO` (o el que
tenga) y el prompt. **No se levanta solo**: espera órdenes. Prueba:

```text
[TUMBADO] orden> l          -> se levanta
[DE_PIE] orden> h           -> saluda
[DE_PIE] orden> t           -> se tumba
[TUMBADO] orden> salir
>>> EL ROBOT QUEDA: TUMBADO
```

### 8.3 Cómo parar el robot (uc10)

1. **`L2` + `B` en el mando**: parada inmediata, siempre lo primero.
2. **`salir`** o **`q`**: si el robot está de pie y estable, **se queda de pie**;
   si no, se tumba. Para dejarlo tumbado, escribe `t` antes de `salir`.
3. **`Ctrl + C` durante una orden**: corta y hace el cierre seguro (se tumba).
4. Al final siempre imprime `>>> EL ROBOT QUEDA: ...`. Si dice que uses el
   mando, hazlo.

---

## 9. Al terminar

1. Robot tumbado (uc10 `t`, o el mando).
2. 📦 `exit` para salir del contenedor.
3. 🖥️ FUERA: `./env/go2_net.sh down` (desactiva la red del robot).
4. Apaga el robot: pulsación corta + larga en la batería, como al encender.

La próxima vez solo necesitas: **5.1** (encender), **5.4** (`up` + ping),
**6** (`./run.sh`) y el caso de uso.

---

## Si falla...

### `permission denied` con docker

`permission denied while trying to connect to the Docker daemon socket`: tu
usuario aún no está en el grupo `docker` en esta sesión.

```bash
groups                    # ¿sale "docker"?
sudo usermod -aG docker $USER
```

y **cierra sesión y vuelve a entrar** (o reinicia). Luego `docker run --rm hello-world`.

Otros `permission denied`:

| ves | haz |
|---|---|
| `./run.sh: Permiso denegado` | `chmod +x run.sh build.sh push.sh env/go2_net.sh` |
| `docker pull`: `denied` / `unauthorized` | escribe la dirección exacta del paso 3; si sigue, `docker logout ghcr.io` y repite |
| no puedes borrar ficheros que creó el contenedor | `sudo chown -R $USER:$USER ~/go2-research-gestos` |

### No hay ping al robot

| comprueba | 🖥️ FUERA |
|---|---|
| ¿está activa la conexión? | `nmcli con show --active` debe listar `go2-eth`. Si no: `./env/go2_net.sh up` |
| ¿tu PC tiene la IP? | `ip -br a show <tu-interfaz>` debe mostrar `192.168.123.222/24` |
| ¿hay enlace físico? | si pone `DOWN` o `NO-CARRIER`: cable mal puesto, robot apagado o aún arrancando (espera 2 min) |
| ¿qué falla exactamente? | `./env/go2_net.sh probe`: "ARP sí / ping no" = IP o máscara mal; "ni ARP ni ping" = cable, puerto o robot |

### Interfaz equivocada

Síntomas: `La interfaz '...' no existe`, `does not match an available interface`,
`No llega rt/sportmodestate`, o no hay ping aunque el cable está bien.

```bash
ip -br a                                              # repite 5.2 (sin y con cable)
nmcli -g connection.interface-name con show go2-eth   # sobre qué interfaz está go2-eth
./env/go2_net.sh create <tu-interfaz-correcta>        # la recrea sobre la buena
./env/go2_net.sh up
sed -i "s/^GO2_IFACE=.*/GO2_IFACE=<tu-interfaz-correcta>/" .env
```

Usa **el mismo nombre** en `./run.sh`, en `--iface` y en `.env`.

### No se abre la ventana

| ves | haz |
|---|---|
| no aparece ventana y no hay error | entraste sin `./run.sh`. Sal (`exit`) y entra con `./run.sh <tu-interfaz>`. 📦 `echo $DISPLAY` debe dar lo mismo que 🖥️ `echo $DISPLAY` (normalmente `:0` o `:1`); si no, 📦 `export DISPLAY=:1` (el de fuera) |
| `cannot connect to X server` / `Authorization required` | 🖥️ FUERA, en otra terminal: `xhost +local:docker`, y repite (no hace falta salir) |
| `xhost: command not found` | 🖥️ `sudo apt install -y x11-xserver-utils` |
| ventana gris o negra, `BadAccess` | 📦 `export QT_X11_NO_MITSHM=1` |
| entras al PC por `ssh` | la pantalla del PC es `:0`: 📦 `export DISPLAY=:0` |

### El robot no hace caso o la cámara falla

| ves | haz |
|---|---|
| código `3205` | la app del móvil o el mando tienen el control: cierra la app del todo y suelta el mando |
| código `3104` | el robot no responde todavía: espera a que termine de arrancar; revisa la red (`probe`) |
| `la camara ha dejado de dar imagen` | app cerrada, cable firme, `./env/go2_net.sh probe`; si sigue, reinicia el robot (tumbado) |
| `No llega rt/sportmodestate` | red o interfaz: ver arriba |
| el gesto no se confirma (el contador no llega) | más lejos (una mano: ~4 m), de frente, quieto, buena luz; baja los brazos entre gesto y gesto |
| `no hago nada (cooldown ...)` | espera 5 s tras cada acción |
| la postura sale `DESCONOCIDO` (uc10) | está cambiando de postura: espera y escribe `estado` |

### Otros

| ves | haz |
|---|---|
| `ERROR: GO2_MODE=real requiere GO2_IFACE` | `./run.sh <tu-interfaz>` o rellena `GO2_IFACE` en `.env` (5.5) |
| `ModuleNotFoundError: No module named 'numpy'` (o `cv2`) | estás FUERA: entra con `./run.sh` |
| `python3: can't open file 'usecases/...'` | 🖥️ `cd ~/go2-research-gestos` antes de `./run.sh`; 📦 `cd /workspace` |
| uc13: `No existe el modelo YOLO: /workspace/yolo11n.pt` | 🖥️ FUERA, con internet: `./tools/get_models.sh` |
| `pip check` dice que `mediapipe` requiere `opencv-contrib-python` | **esperado: no lo instales** (rompería las ventanas) |

Guía completa con todos los detalles: [`GUIA_MIS_CASOS_USO.md`](GUIA_MIS_CASOS_USO.md).
Casos de uso: [uc13](../usecases/uc13_gesture_follow/README.md) ·
[uc10](../usecases/uc10_text_commands/README.md).
